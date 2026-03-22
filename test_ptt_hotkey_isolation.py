"""PTT切り分けテスト — ホットキースレッドの影響を検証

テスト1: ホットキースレッドなしで録音 (2秒)
テスト2: ホットキースレッド起動後に録音 (2秒)
テスト3: tkinter mainloop内から録音 (2秒)

各テストの間にEnterキーで進む。マイクに向かって話しながら実行。
"""
import sounddevice as sd
import numpy as np
import threading
import ctypes
import ctypes.wintypes
import time

dev = sd.default.device[0]
info = sd.query_devices(dev)
rate = int(info['default_samplerate'])
print(f"Device #{dev}: {info['name']} rate={rate}")

def do_record(label):
    print(f"\n=== {label}: recording 2s... ===")
    audio = sd.rec(int(rate * 2), samplerate=rate, channels=1, dtype="int16", device=dev)
    sd.wait()
    peak = int(np.max(np.abs(audio)))
    print(f"  peak={peak} {'OK' if peak > 100 else 'FAIL - SILENT'}")
    return peak

# --- Test 1: No hotkey thread ---
input("\nTest 1: ホットキーなし。Enterを押して2秒間話す...")
do_record("Test 1 (no hotkey)")

# --- Start hotkey thread (same as ritsu_v4.py) ---
def start_hotkey_thread():
    def _thread():
        user32 = ctypes.windll.user32
        LRESULT = ctypes.c_longlong
        WPARAM = ctypes.c_ulonglong
        LPARAM = ctypes.c_longlong
        HOOKPROC = ctypes.CFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("pt_x", ctypes.c_long), ("pt_y", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("flags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, ctypes.c_ulong]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, WPARAM, LPARAM]
        user32.CallNextHookEx.restype = LRESULT

        def mouse_proc(nCode, wParam, lParam):
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        proc = HOOKPROC(mouse_proc)
        hook = user32.SetWindowsHookExW(14, proc, None, 0)  # WH_MOUSE_LL
        print(f"  Mouse hook installed: {bool(hook)}")

        VK_F10 = 0x79
        user32.RegisterHotKey(None, 1, 0, VK_F10)
        print(f"  F10 hotkey registered")

        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                pass
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    t = threading.Thread(target=_thread, daemon=True, name="hotkey")
    t.start()
    time.sleep(0.5)  # wait for hook to install
    return t

print("\nStarting hotkey thread...")
start_hotkey_thread()

# --- Test 2: After hotkey thread ---
input("\nTest 2: ホットキースレッド起動後。Enterを押して2秒間話す...")
do_record("Test 2 (after hotkey)")

# --- Test 3: From tkinter after() ---
print("\nTest 3: tkinter mainloop内。ウィンドウが出たら2秒間話す...")
import tkinter as tk
root = tk.Tk()
root.title("PTT Test")
root.geometry("300x100")
label = tk.Label(root, text="Starting test 3...", font=("Segoe UI", 12))
label.pack(pady=20)

def test3():
    def _rec():
        peak = do_record("Test 3 (tkinter mainloop)")
        root.after(0, lambda: label.config(text=f"Test 3: peak={peak}"))
        root.after(2000, root.destroy)
    threading.Thread(target=_rec, daemon=True).start()

root.after(500, test3)
root.mainloop()

print("\n=== All tests done ===")
