from __future__ import annotations

import os
import sys
import tracemalloc
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    label: str
    pid: int
    rss_bytes: int | None
    peak_traced_bytes: int | None
    current_traced_bytes: int | None
    source: str

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def start_memory_trace() -> None:
    if not tracemalloc.is_tracing():
        tracemalloc.start(25)


def memory_snapshot(label: str) -> MemorySnapshot:
    rss, source = process_rss_bytes()
    current = peak = None
    if tracemalloc.is_tracing():
        current, peak = tracemalloc.get_traced_memory()
    return MemorySnapshot(
        label=label,
        pid=os.getpid(),
        rss_bytes=rss,
        peak_traced_bytes=peak,
        current_traced_bytes=current,
        source=source,
    )


def process_rss_bytes() -> tuple[int | None, str]:
    if sys.platform.startswith("win"):
        return windows_process_rss_bytes()
    return unix_process_rss_bytes()


def windows_process_rss_bytes() -> tuple[int | None, str]:
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        if not ok:
            return None, "windows_psapi_failed"
        return int(counters.WorkingSetSize), "windows_working_set"
    except Exception:
        return None, "windows_unavailable"


def unix_process_rss_bytes() -> tuple[int | None, str]:
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss = int(usage.ru_maxrss)
        if sys.platform == "darwin":
            return rss, "resource_ru_maxrss_bytes"
        return rss * 1024, "resource_ru_maxrss_kib"
    except Exception:
        return None, "unix_unavailable"
