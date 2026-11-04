"""Bounded review boundary in the live optimizer; no replay or resource teardown."""
import json
import time
from contextlib import contextmanager
from pathlib import Path
from .publication import write_json

SETTINGS=dict(version='training-evidence-review-v1',interval=5,seconds=120,retain_residency=True)


def enabled(job):
    value=job.get('review_window')
    if value is not None and value!=SETTINGS:raise ValueError('Unknown bounded review window')
    return value is not None


def due(job,generation):
    return enabled(job) and 0<generation<job['phase2_generations'] and generation%5==0


@contextmanager
def locked(review, *, timeout=120.):
    import errno
    import msvcrt
    review=Path(review);review.mkdir(parents=True,exist_ok=True)
    with (review/'window.lock').open('a+b') as handle:
        # Windows permits locking beyond EOF. Never read/write the lock byte:
        # another process may already own it, including during first creation.
        deadline=time.monotonic()+timeout
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                break
            except OSError as error:
                if error.errno not in (errno.EACCES,errno.EAGAIN,errno.EDEADLK):raise
                if time.monotonic()>=deadline:
                    raise TimeoutError('Review publication lock did not release') from error
                time.sleep(.05)
        try:yield
        finally:handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)


def wait_for_review(root,job,generation,publish,*,clock=time.time,sleep=time.sleep,exporter=None):
    if not due(job,generation):return
    from .campaign_runner import stop_check
    from .agent_review_packet import export
    root=Path(root);review=root/'agent-reviews';packet=review/f'epoch-{generation:04d}'
    state_path=packet/'window.json';packet.mkdir(parents=True,exist_ok=True)
    # A restart never renews an expired deadline or reopens a completed window.
    if state_path.exists():
        state=json.loads(state_path.read_bytes())
        if state['status'] not in ('preparing','reviewing'):return state
    else:
        state=dict(version=SETTINGS['version'],review_epoch=generation,source_generation=generation,
                   status='preparing',seconds=120,retain_residency=True)
        write_json(state_path,state)
    stop_check([job['campaign']])
    if state['status']=='preparing':
        publish(review_status='Preparing existing training evidence',review_epoch=generation)
        (exporter or export)(root,job,generation,packet)
        with locked(review):
            started=clock();state.update(status='reviewing',opened_epoch=started,deadline_epoch=started+120)
            write_json(state_path,state);write_json(review/'active-window.json',dict(state,packet=str(packet)))
    while True:
        stop_check([job['campaign']])
        with locked(review):
            state=json.loads(state_path.read_bytes())
            if state['status'] in ('submitted','no_proposals'):return state
            remaining=state['deadline_epoch']-clock()
            if remaining<=0:
                state.update(status='timed_out',closed_epoch=clock(),reason='No checked submission within120 seconds; normal evolution continues.')
                write_json(state_path,state);write_json(review/'active-window.json',dict(state,packet=str(packet)))
                publish(review_status='Review timed out; continuing',review_epoch=generation,review_seconds_remaining=0)
                return state
        publish(review_status='Review window; RAM/GPU state retained',review_epoch=generation,
                review_seconds_remaining=round(remaining,1),completed_generations=generation)
        sleep(min(1,remaining))
