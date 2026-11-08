"""Generator-only Windows resource scheduling; never changes another process."""

import ctypes
from contextlib import contextmanager
import os


def background(enabled):
    """Lower the current worker's CPU, I/O and memory scheduling priorities.

    PROCESS_MODE_BACKGROUND_BEGIN is the documented Windows background-work
    API. It applies only to this process and its threads. Fail visibly rather
    than silently running a supposedly background producer at normal priority.
    https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-setpriorityclass
    """
    if not enabled:
        return
    if os.name != "nt":
        raise OSError("Background worker scheduling requires Windows")
    set_mode(0x00100000)


def set_mode(flag):
    """Apply a documented background begin/end flag to this process only."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    kernel.SetPriorityClass.restype = ctypes.c_int
    if not kernel.SetPriorityClass(ctypes.c_void_p(-1), flag):
        raise ctypes.WinError(ctypes.get_last_error())


@contextmanager
def publication(enabled):
    """Yield foreground resource priority only while writing the payload.

    Restore priority before reading ordinal metadata or handing off receipts;
    backgrounding those operations would serialize the entire coordinator.
    """
    if not enabled:
        yield
        return
    background(True)
    try:
        yield
    finally:
        set_mode(0x00200000)
