"""Short shared progress reads; transient Windows access errors stay display-only."""
import json,os
from contextlib import contextmanager


@contextmanager
def shared_binary(path):
    if os.name!='nt':
        with open(path,'rb') as stream:yield stream
        return
    import ctypes,msvcrt
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    create=kernel.CreateFileW;create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE];create.restype=wintypes.HANDLE
    handle=create(str(path),0x80000000,7,None,3,128,None)  # Read/write/delete sharing.
    if handle==wintypes.HANDLE(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    try:fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
    except BaseException:
        kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle(handle);raise
    with os.fdopen(fd,'rb') as stream:yield stream


def read_progress(path,previous=None):
    """A failed status read is visible stale display data, never a GA failure.

    Checkpoints, seals, receipts and financial data keep their strict readers.
    """
    try:
        with shared_binary(path) as stream:return json.load(stream)
    except (OSError,ValueError) as error:
        return dict(previous or {},progress_stale=True,progress_read_error=f'{type(error).__name__}: {error}')
