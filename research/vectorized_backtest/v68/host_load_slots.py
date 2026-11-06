"""Machine-wide preparation admission shared by training and validation lanes."""
from contextlib import contextmanager


@contextmanager
def slot(config,check_stop):
    semaphore=config.get('host_load_slots');counts=config.get('host_load_counts')
    if semaphore is None:
        yield;return
    check_stop()
    while not semaphore.acquire(timeout=.2):check_stop()
    with counts.get_lock():
        counts[0]+=1;counts[1]=max(counts[1],counts[0]);counts[2]+=1
    try:yield
    finally:
        with counts.get_lock():counts[0]-=1;counts[3]+=1
        semaphore.release()
