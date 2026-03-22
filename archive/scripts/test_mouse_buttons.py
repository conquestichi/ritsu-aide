#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
マウスボタン認識テスト
XButton1/XButton2がどのように認識されるか確認
"""
import sys

print("=== Mouse Button Test ===")
print("マウスボタンを押してください（Ctrl+Cで終了）")
print()

# Test 1: pynput
try:
    from pynput import mouse
    print("[pynput] Testing...")

    def on_click_pynput(x, y, button, pressed):
        action = "pressed" if pressed else "released"
        print(f"[pynput] {action}: button={button}, button.name={getattr(button, 'name', 'N/A')}, value={getattr(button, 'value', 'N/A')}")

        # Check if it's XButton1 or XButton2
        if hasattr(mouse.Button, 'x1') and button == mouse.Button.x1:
            print(f"  → Detected as XButton1")
        if hasattr(mouse.Button, 'x2') and button == mouse.Button.x2:
            print(f"  → Detected as XButton2")

    listener = mouse.Listener(on_click=on_click_pynput)
    listener.start()
    print("[pynput] Listener started")
except ImportError as e:
    print(f"[pynput] Not available: {e}")
except Exception as e:
    print(f"[pynput] Error: {e}")

# Test 2: Direct Windows API
try:
    import ctypes
    from ctypes import wintypes
    import threading

    print("\n[WinAPI] Testing...")

    user32 = ctypes.windll.user32
    WH_MOUSE_LL = 14
    WM_LBUTTONDOWN = 0x0201
    WM_LBUTTONUP = 0x0202
    WM_RBUTTONDOWN = 0x0204
    WM_RBUTTONUP = 0x0205
    WM_MBUTTONDOWN = 0x0207
    WM_MBUTTONUP = 0x0208
    WM_XBUTTONDOWN = 0x020B
    WM_XBUTTONUP = 0x020C

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("pt", wintypes.POINT),
            ("mouseData", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))
        ]

    def low_level_mouse_handler(nCode, wParam, lParam):
        if nCode >= 0:
            event = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents

            if wParam == WM_XBUTTONDOWN or wParam == WM_XBUTTONUP:
                action = "DOWN" if wParam == WM_XBUTTONDOWN else "UP"
                # XButton1 = 0x0001, XButton2 = 0x0002 in high word of mouseData
                xbutton = (event.mouseData >> 16) & 0xFFFF
                button_name = "XButton1" if xbutton == 1 else "XButton2" if xbutton == 2 else f"Unknown({xbutton})"
                print(f"[WinAPI] {action}: {button_name} (mouseData={event.mouseData:#010x}, xbutton={xbutton})")

        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    hook_proc = HOOKPROC(low_level_mouse_handler)

    hook_id = user32.SetWindowsHookExW(WH_MOUSE_LL, hook_proc, None, 0)
    if hook_id:
        print(f"[WinAPI] Hook installed (ID: {hook_id})")

        # Message loop
        msg = wintypes.MSG()
        def message_loop():
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

        msg_thread = threading.Thread(target=message_loop, daemon=True)
        msg_thread.start()
    else:
        print("[WinAPI] Failed to install hook")

except Exception as e:
    print(f"[WinAPI] Error: {e}")
    import traceback
    traceback.print_exc()

print("\nPress Ctrl+C to exit...")

try:
    import time
    while True:
        time.sleep(0.1)
except KeyboardInterrupt:
    print("\nExiting...")
    sys.exit(0)
