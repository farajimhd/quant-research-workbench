"""Synchronized duty-cycle pacing; never changes optimizer or data semantics."""
import math
import time


class LaptopGpuPacer:
    def __init__(self, device, *, duty_cycle=.75, reserve_bytes=4*1024**3,
                 clock=time.perf_counter, sleep=time.sleep, cuda=None,
                 system_reserve_bytes=8*1024**3, system_available=None):
        if not math.isfinite(duty_cycle) or not 0 < duty_cycle < 1:
            raise ValueError('GPU duty cycle must be strictly between zero and one')
        if not isinstance(reserve_bytes, int) or reserve_bytes <= 0:
            raise ValueError('Positive VRAM reserve required')
        if not isinstance(system_reserve_bytes, int) or system_reserve_bytes <= 0:
            raise ValueError('Positive system RAM reserve required')
        if system_available is None:
            import psutil
            system_available = lambda: psutil.virtual_memory().available
        self.system_available = system_available
        self.system_reserve_bytes = system_reserve_bytes
        if cuda is None:
            import torch
            cuda = torch.cuda
        self.device, self.cuda = device, cuda
        self.duty_cycle, self.reserve_bytes = duty_cycle, reserve_bytes
        self.clock, self.sleep = clock, sleep
        self.started = clock()

    def begin_activity(self):
        """Exclude loading/idle time before a chronological compute chunk."""
        free = self.check_reserve()
        self.started = self.clock()
        return free

    def check_reserve(self):
        available = self.system_available()
        if available < self.system_reserve_bytes:
            raise RuntimeError(f'Laptop system RAM reserve breached: {available} available bytes; '
                               f'{self.system_reserve_bytes} required')
        self.cuda.synchronize(self.device)
        free, total = self.cuda.mem_get_info(self.device)
        if free < self.reserve_bytes:
            raise RuntimeError(f'Laptop VRAM reserve breached: {free} free bytes; '
                               f'{self.reserve_bytes} required; {total} total')
        return free

    def __call__(self, progress=None):
        free = self.check_reserve()
        elapsed = max(0., self.clock()-self.started)
        pause = elapsed*(1/self.duty_cycle-1)
        self.sleep(pause)
        self.started = self.clock()
        return dict(active_seconds=elapsed, pause_seconds=pause, free_bytes=free)
