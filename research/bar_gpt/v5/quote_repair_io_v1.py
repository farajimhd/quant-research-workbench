"""Thread-local Windows scheduling for repair payload reads and publications.

Only the calling repair thread is affected, never the trainer or whole process.
Canonical queries and shared cache locks remain outside the background context.
https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-setthreadpriority
"""

from contextlib import contextmanager
import ctypes
import os


def set_mode(flag):
    """Apply background begin/end to this thread; fail visibly on other errors."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.SetThreadPriority.argtypes = (ctypes.c_void_p, ctypes.c_int)
    kernel.SetThreadPriority.restype = ctypes.c_int
    if not kernel.SetThreadPriority(ctypes.c_void_p(-2), flag):
        error = ctypes.get_last_error()
        if flag == 0x00010000 and error == 400:
            return False  # Already background: preserve its existing mode.
        raise ctypes.WinError(error)
    return True


@contextmanager
def payload_io():
    """Lower the current payload reader/writer; restore even on exceptions."""
    if os.name != "nt":
        yield
        return
    entered = set_mode(0x00010000)
    try:
        yield
    finally:
        if entered:
            set_mode(0x00020000)
