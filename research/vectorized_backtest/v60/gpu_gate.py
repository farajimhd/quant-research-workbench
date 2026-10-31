"""Host-local GPU turns for independent session processes; no shared replay state."""
import os,time,hashlib
from contextlib import contextmanager

class ReplayGate:
    def __init__(self,name,check_stop=lambda:None):
        if os.name!='nt':raise ValueError('This campaign execution gate requires Windows')
        import ctypes
        from ctypes import wintypes
        self.api=ctypes.WinDLL('kernel32',use_last_error=True)
        self.api.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR];self.api.CreateMutexW.restype=wintypes.HANDLE
        self.api.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];self.api.WaitForSingleObject.restype=wintypes.DWORD
        self.api.ReleaseMutex.argtypes=[wintypes.HANDLE];self.api.CloseHandle.argtypes=[wintypes.HANDLE]
        self.handle=self.api.CreateMutexW(None,False,'Local\\V60Replay-'+hashlib.sha256(name.encode()).hexdigest()[:24])
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
        self.check_stop=check_stop;self.wait_seconds=0.

    @contextmanager
    def turn(self):
        started=time.perf_counter()
        while True:
            self.check_stop();result=self.api.WaitForSingleObject(self.handle,100)
            if result in (0,0x80):break
            if result!=258:raise OSError('GPU execution gate wait failed')
        self.wait_seconds+=time.perf_counter()-started
        try:yield
        finally:self.api.ReleaseMutex(self.handle)

    def close(self):
        if self.handle:self.api.CloseHandle(self.handle);self.handle=None

def enqueue_turns(graph,total,block,gate,device,progress=None):
    """At most 512 chronological clocks per turn; numerical order is unchanged."""
    import torch
    chunk=max(block,(512//block)*block)
    for begin in range(0,total,chunk):
        with gate.turn():
            for _ in range(begin,min(total,begin+chunk),block):graph.replay()
            torch.cuda.current_stream(device).synchronize()
        if progress:progress(min(total,begin+chunk),total)
