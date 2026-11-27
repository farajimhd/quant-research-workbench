"""Read mouse and navigation keys from a native Windows console."""
import ctypes
from ctypes import wintypes
from contextlib import contextmanager

import os


class KeyEvent(ctypes.Structure):
    _fields_ = [("down", wintypes.BOOL), ("repeat", wintypes.WORD),
                ("key", wintypes.WORD), ("scan", wintypes.WORD),
                ("char", wintypes.WCHAR), ("modifiers", wintypes.DWORD)]


class MouseEvent(ctypes.Structure):
    _fields_ = [("x", wintypes.SHORT), ("y", wintypes.SHORT),
                ("buttons", wintypes.DWORD), ("modifiers", wintypes.DWORD),
                ("flags", wintypes.DWORD)]


class InputEvent(ctypes.Union):
    _fields_ = [("key", KeyEvent), ("mouse", MouseEvent)]


class InputRecord(ctypes.Structure):
    _fields_ = [("kind", wintypes.WORD), ("event", InputEvent)]


class ScrollInput:
    def __init__(self, api, handle):
        self.api = api
        self.handle = handle
        self.records = (InputRecord * 32)()

    def read(self, page):
        """Return navigation in row units, with Home/End clamped by the viewport."""
        waiting = wintypes.DWORD()
        if not self.api.GetNumberOfConsoleInputEvents(self.handle, ctypes.byref(waiting)) or not waiting.value:
            return []
        count = wintypes.DWORD()
        if not self.api.ReadConsoleInputW(self.handle, self.records, min(waiting.value, len(self.records)),
                                          ctypes.byref(count)):
            return []
        keys = {0x21: -page, 0x22: page, 0x26: -1, 0x28: 1}
        actions = []
        for record in self.records[:count.value]:
            if record.kind == 1 and record.event.key.down:
                key = record.event.key.key
                if key == 0x24:
                    actions.append("home")
                elif key == 0x23:
                    actions.append("end")
                elif key in keys:
                    actions.append(keys[key] * record.event.key.repeat)
            elif record.kind == 2 and record.event.mouse.flags & 0x0004:
                delta = ctypes.c_short(record.event.mouse.buttons >> 16).value
                if delta:
                    actions.append(-3 * (1 if delta > 0 else -1) * max(1, abs(delta) // 120))
        return actions


@contextmanager
def console_scroll_input(enabled):
    if not enabled or os.name != "nt":
        yield None
        return
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetStdHandle.argtypes = [wintypes.DWORD]
    api.GetStdHandle.restype = wintypes.HANDLE
    api.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    api.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.GetNumberOfConsoleInputEvents.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    api.ReadConsoleInputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(InputRecord),
                                      wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    handle = api.GetStdHandle(-10)
    mode = wintypes.DWORD()
    # Turn on mouse reporting and turn off Quick Edit so wheel events reach
    # the panel. Extended flags let Windows apply the Quick Edit setting.
    if not api.GetConsoleMode(handle, ctypes.byref(mode)) or not api.SetConsoleMode(
            handle, (mode.value | 0x0090) & ~0x0040):
        yield None
        return
    try:
        yield ScrollInput(api, handle)
    finally:
        api.SetConsoleMode(handle, mode.value)
