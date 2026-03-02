#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Windows Mouse Hook - All Messages Debug
すべてのマウスメッセージを詳細ログ出力
"""
import ctypes
from ctypes import wintypes
import sys
import time
import threading

print("=" * 60)
print("Windows Mouse Hook - All Messages Debug")
print("=" * 60)
print("マウスを動かしたり、ボタンを押してください")
print("特にXButton1/XButton2（サイドボタン）をテストしてください")
print("Ctrl+Cで終了")
print("=" * 60)
print()

# Windows API
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Hook constants
WH_MOUSE_LL = 14

# Mouse messages
MOUSE_MESSAGES = {
    0x0200: "WM_MOUSEMOVE",
    0x0201: "WM_LBUTTONDOWN",
    0x0202: "WM_LBUTTONUP",
    0x0203: "WM_LBUTTONDBLCLK",
    0x0204: "WM_RBUTTONDOWN",
    0x0205: "WM_RBUTTONUP",
    0x0206: "WM_RBUTTONDBLCLK",
    0x0207: "WM_MBUTTONDOWN",
    0x0208: "WM_MBUTTONUP",
    0x0209: "WM_MBUTTONDBLCLK",
    0x020A: "WM_MOUSEWHEEL",
    0x020B: "WM_XBUTTONDOWN",
    0x020C: "WM_XBUTTONUP",
    0x020D: "WM_XBUTTONDBLCLK",
    0x020E: "WM_MOUSEHWHEEL",
}

# MSLLHOOKSTRUCT
class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))
    ]

# Stats
message_count = {}
last_move_time = 0
move_suppress_ms = 100  # Suppress frequent MOUSEMOVE logs

def low_level_mouse_handler(nCode, wParam, lParam):
    global last_move_time

    if nCode >= 0:
        try:
            event = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            msg_name = MOUSE_MESSAGES.get(wParam, f"Unknown(0x{wParam:04X})")

            # Count messages
            message_count[msg_name] = message_count.get(msg_name, 0) + 1

            # Suppress frequent MOUSEMOVE
            if wParam == 0x0200:  # WM_MOUSEMOVE
                now = time.time() * 1000
                if now - last_move_time < move_suppress_ms:
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)
                last_move_time = now

            # Build log message
            log_parts = [
                f"[{msg_name}]",
                f"pos=({event.pt.x}, {event.pt.y})",
                f"mouseData=0x{event.mouseData:08X}",
                f"flags=0x{event.flags:08X}",
            ]

            # Decode XButton info
            if wParam in (0x020B, 0x020C, 0x020D):  # XBUTTON messages
                xbutton = (event.mouseData >> 16) & 0xFFFF
                button_name = "XButton1" if xbutton == 1 else "XButton2" if xbutton == 2 else f"Unknown({xbutton})"
                log_parts.append(f"→ {button_name}")

            # Decode wheel delta
            if wParam in (0x020A, 0x020E):  # MOUSEWHEEL, MOUSEHWHEEL
                delta = ctypes.c_short(event.mouseData >> 16).value
                log_parts.append(f"delta={delta}")

            print(" ".join(log_parts))

        except Exception as e:
            print(f"[ERROR] Handler exception: {e}")

    return user32.CallNextHookEx(None, nCode, wParam, lParam)

# Install hook
HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
hook_proc = HOOKPROC(low_level_mouse_handler)
hook_id = user32.SetWindowsHookExW(WH_MOUSE_LL, hook_proc, None, 0)

if not hook_id:
    print("[ERROR] Failed to install mouse hook!")
    sys.exit(1)

print(f"[OK] Mouse hook installed (ID: {hook_id})")
print()

# Message pump
def message_pump():
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

pump_thread = threading.Thread(target=message_pump, daemon=True)
pump_thread.start()

# Stats display
def show_stats():
    while True:
        time.sleep(5)
        if message_count:
            print("\n--- Message Count (last 5s) ---")
            for msg, count in sorted(message_count.items(), key=lambda x: x[1], reverse=True):
                print(f"  {msg}: {count}")
            message_count.clear()
            print()

stats_thread = threading.Thread(target=show_stats, daemon=True)
stats_thread.start()

# Keep running
try:
    while True:
        time.sleep(0.1)
except KeyboardInterrupt:
    print("\n[EXIT] Unhooking...")
    user32.UnhookWindowsHookEx(hook_id)
    print("[EXIT] Done.")
    sys.exit(0)
