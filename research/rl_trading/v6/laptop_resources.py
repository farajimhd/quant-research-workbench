"""Synchronized duty-cycle pacing; never changes optimizer or data semantics."""
import math
import time


class LaptopGpuPacer:
    def __init__(self, device, *, duty_cycle=.75, reserve_bytes=4*1024**3,
                 clock=time.perf_counter, sleep=time.sleep, cuda=None):
        if not math.isfinite(duty_cycle) or not 0 < duty_cycle < 1:
            raise ValueError('GPU duty cycle must be strictly between zero and one')
        if not isinstance(reserve_bytes, int) or reserve_bytes <= 0:
            raise ValueError('Positive VRAM reserve required')
        if cuda is None:
            import torch
            cuda = torch.cuda
        self.device, self.cuda = device, cuda
        self.duty_cycle, self.reserve_bytes = duty_cycle, reserve_bytes
        self.clock, self.sleep = clock, sleep
        self.started = clock()

    def __call__(self, progress=None):
        self.cuda.synchronize(self.device)
        free, total = self.cuda.mem_get_info(self.device)
        if free < self.reserve_bytes:
            raise RuntimeError(f'Laptop VRAM reserve breached: {free} free bytes; '
                               f'{self.reserve_bytes} required; {total} total')
        elapsed = max(0., self.clock()-self.started)
        pause = elapsed*(1/self.duty_cycle-1)
        self.sleep(pause)
        self.started = self.clock()
        return dict(active_seconds=elapsed, pause_seconds=pause, free_bytes=free)
