"""Read NTFS/ReFS change sequence, without relying on coarse write timestamps.

Unsupported filesystems return None: callers must hash contents, not cache by stat.
https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-fsctl_read_file_usn_data
"""
import os
import struct
from functools import lru_cache


@lru_cache(maxsize=1)
def api():
    import ctypes as c
    from ctypes import wintypes as w
    kernel=c.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
    kernel.CreateFileW.restype=w.HANDLE
    kernel.DeviceIoControl.argtypes=[w.HANDLE,w.DWORD,c.c_void_p,w.DWORD,c.c_void_p,w.DWORD,c.POINTER(w.DWORD),c.c_void_p]
    kernel.DeviceIoControl.restype=w.BOOL
    kernel.CloseHandle.argtypes=[w.HANDLE]
    kernel.CloseHandle.restype=w.BOOL
    return kernel


def change_version(path):
    if os.name!='nt':return None
    import ctypes as c
    from ctypes import wintypes as w
    kernel=api();handle=kernel.CreateFileW(str(path),0x80,7,None,3,0,None)
    if handle==c.c_void_p(-1).value:return None
    try:
        output=c.create_string_buffer(4096);used=w.DWORD()
        versions=c.create_string_buffer(struct.pack('<HH',2,3))
        if not kernel.DeviceIoControl(handle,0x900eb,versions,4,output,len(output),c.byref(used),None):return None
        if used.value<8:return None
        length,major,_=struct.unpack_from('<IHH',output.raw)
        offset={2:24,3:40}.get(major)
        if offset is None or not offset+8<=length<=used.value:return None
        usn=struct.unpack_from('<q',output.raw,offset)[0]
        return (major,usn) if usn else None
    finally:kernel.CloseHandle(handle)
