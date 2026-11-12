"""Restore NORMAL memory priority for the owned phase-two coordinator only.

Task Scheduler priority7 gives the coordinator LOW memory priority even with
ample free RAM. This operation leaves CPU priority, workers, queries and all
data/checkpoint contracts unchanged. It does not lock pages or change OS limits.
"""

import argparse
import ctypes
import json
from pathlib import Path
import time

import psutil


def reserve_ok(available, private, query_reserved, reserve):
    return available >= reserve + query_reserved + private


def set_memory_priority(pid, created, available, reserved, reserve, *, apply=False, retain_working_set=False):
    p=psutil.Process(pid)
    if abs(p.create_time()-created)>.01:
        raise ValueError('Coordinator PID identity changed')
    if 'phase2_ar_shards.run' not in ' '.join(p.cmdline()):
        raise ValueError('Process is not the phase-two data coordinator')
    cpu_priority=p.nice();info=p.memory_info()
    if not reserve_ok(available,info.private,reserved,reserve):
        raise MemoryError('Cannot retain coordinator indices within RAM reserve')
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.GetProcessInformation.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    kernel.SetProcessInformation.argtypes=kernel.GetProcessInformation.argtypes
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x100|0x200|0x400|0x1000,False,pid)
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    priority=wintypes.ULONG()
    try:
        if not kernel.GetProcessInformation(handle,0,ctypes.byref(priority),ctypes.sizeof(priority)):
            raise ctypes.WinError(ctypes.get_last_error())
        before=priority.value
        if apply and before!=5:
            priority.value=5
            if not kernel.SetProcessInformation(handle,0,ctypes.byref(priority),ctypes.sizeof(priority)):
                raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.GetProcessInformation(handle,0,ctypes.byref(priority),ctypes.sizeof(priority)):
            raise ctypes.WinError(ctypes.get_last_error())
        if apply and priority.value!=5:raise RuntimeError('Memory priority readback differs')
        assert p.nice()==cpu_priority,'CPU priority changed'
        assert abs(p.create_time()-created)<.01
        working_set = None
        if retain_working_set:
            minimum=((info.private+1024**3-1)//1024**3)*1024**3
            if not reserve_ok(available,minimum,reserved,reserve):
                raise MemoryError('Working-set floor would consume RAM reserve')
            lo,hi=ctypes.c_size_t(),ctypes.c_size_t();flags=wintypes.DWORD()
            kernel.GetProcessWorkingSetSizeEx.argtypes=[wintypes.HANDLE,ctypes.POINTER(ctypes.c_size_t),ctypes.POINTER(ctypes.c_size_t),ctypes.POINTER(wintypes.DWORD)]
            kernel.SetProcessWorkingSetSizeEx.argtypes=[wintypes.HANDLE,ctypes.c_size_t,ctypes.c_size_t,wintypes.DWORD]
            if not kernel.GetProcessWorkingSetSizeEx(handle,ctypes.byref(lo),ctypes.byref(hi),ctypes.byref(flags)):
                raise ctypes.WinError(ctypes.get_last_error())
            working_set=dict(before=dict(minimum=lo.value,maximum=hi.value,flags=flags.value))
            if apply:
                # Per-process hard minimum; no hard maximum and no VirtualLock.
                if not kernel.SetProcessWorkingSetSizeEx(handle,minimum,max(hi.value,minimum*2),9):
                    raise ctypes.WinError(ctypes.get_last_error())
                if not kernel.GetProcessWorkingSetSizeEx(handle,ctypes.byref(lo),ctypes.byref(hi),ctypes.byref(flags)):
                    raise ctypes.WinError(ctypes.get_last_error())
                assert lo.value>=minimum and flags.value&1
            working_set['after']=dict(minimum=lo.value,maximum=hi.value,flags=flags.value)
        return dict(pid=pid,created=created,memory_priority_before=before,
                    memory_priority_after=priority.value,cpu_priority_unchanged=cpu_priority,
                    private_bytes=info.private,resident_bytes=info.rss,applied=apply,working_set=working_set)
    finally:kernel.CloseHandle(handle)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign',type=Path,required=True)
    p.add_argument('--apply',action='store_true')
    p.add_argument('--retain-working-set',action='store_true')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(argv)
    read=lambda path:json.loads(path.read_text())
    owner=read(args.campaign/'child.json');root=Path(owner['output'])
    settings=read(root/'execution.json');progress=read(root/'progress.json')
    assert progress['state']=='running' and progress['failure'] is None
    assert time.time()-progress['updated']<15
    report=set_memory_priority(owner['pid'],owner['created'],psutil.virtual_memory().available,
        int(progress.get('reserved_query_gib',0)*1024**3),
        int(settings['reserve_memory_gib']*1024**3),apply=args.apply,retain_working_set=args.retain_working_set)
    from .shards.contract import write_json
    report.update(checked=time.time(),campaign=str(args.campaign),training_untouched=True)
    write_json(args.output,report)
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
