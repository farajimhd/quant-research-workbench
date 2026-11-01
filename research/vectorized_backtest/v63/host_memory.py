"""Host-memory admission for workers sharing a machine with other workloads."""
from pathlib import Path
import time
import psutil


def wait_for_headroom(output, emit, *, reserve_gib=128., admission_gib=32.,
                      snapshot=psutil.virtual_memory, sleep=time.sleep, boundary_stop=None):
    """Pause before allocating a session; never stop or alter another job.

    This is a soft admission guard, not an OS-enforced RSS limit. The reserve
    includes a conservative session-load allowance and remains free for peers.
    """
    if reserve_gib <= 0:
        return
    required=(reserve_gib+admission_gib)*1024**3
    while snapshot().available < required:
        # A global evolution STOP is consumed after the generation is sealed.
        # Ancestor/root cancellation remains immediate during admission waits.
        if any((p/'STOP').exists() and (boundary_stop is None or p!=Path(boundary_stop))
               for p in (Path(output), *Path(output).parents)):
            raise InterruptedError('Stopped while waiting for host RAM headroom')
        emit(stage='Waiting for RAM headroom', required_available_gib=reserve_gib+admission_gib,
             available_gib=snapshot().available/1024**3)
        sleep(2.)
