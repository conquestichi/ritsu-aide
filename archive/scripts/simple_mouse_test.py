#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simple Mouse Hook Test - Minimal implementation
"""
import ctypes
from ctypes import wintypes
import time

print("Simple Mouse Hook Test")
print("Press XButton1 or XButton2 (mouse side buttons)")
print("Press Ctrl+C to exit")
print()

user32 = ctypes.windll.user32

# Configure CallNextHookEx argtypes/restype for 64-bit correctness
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.wintypes.LPARAM
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.wintypes.DWORD]
user32.SetWindowsHookExW.restype = ctypes.c_void_p

# Hook constants
WH_MOUSE_LL = 14
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C

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

# Global to keep callback alive
_hook_proc = None
_hook_id = None

def mouse_callback(nCode, wParam, lParam):
    if nCode >= 0:
        if wParam == WM_XBUTTONDOWN or wParam == WM_XBUTTONUP:
            event = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            xbutton = (event.mouseData >> 16) & 0xFFFF
            action = "DOWN" if wParam == WM_XBUTTONDOWN else "UP"
            button = "XButton1" if xbutton == 1 else "XButton2" if xbutton == 2 else f"Unknown({xbutton})"
            print(f"[{action}] {button}")

    return user32.CallNextHookEx(None, nCode, wParam, lParam)

# Install hook
HOOKPROC = ctypes.WINFUNCTYPE(ctypes.wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_hook_proc = HOOKPROC(mouse_callback)
_hook_id = user32.SetWindowsHookExW(WH_MOUSE_LL, _hook_proc, None, 0)

if not _hook_id:
    print("ERROR: Failed to install hook")
    import sys
    sys.exit(1)

print(f"Hook installed (ID: {_hook_id})")
print()

# Message loop
msg = wintypes.MSG()
try:
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
except KeyboardInterrupt:
    print("\nExiting...")
finally:
    if _hook_id:
        user32.UnhookWindowsHookEx(_hook_id)
