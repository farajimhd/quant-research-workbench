"""Execution scheduling only; never a genome, score, or replay identity input."""
DEFAULT=dict(time_block=256,transfer_mode='synchronous')


def tuning(job):
    value=dict(DEFAULT,**job.get('replay_tuning',{}))
    if set(value)!=set(DEFAULT) or value['time_block'] not in (256,512,1024):
        raise ValueError('Unsupported independent-clock block')
    if value['transfer_mode'] not in ('synchronous','events'):
        raise ValueError('Unsupported replay transfer scheduling')
    return value


def configuration(job):
    workers=job.get('global_session_workers',2)
    if workers not in (2,4):raise ValueError('Replay workers must be two or four')
    return dict(session_workers=workers,**tuning(job))
