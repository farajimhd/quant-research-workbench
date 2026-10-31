"""V60 always evaluates fresh founders; prior-version result import is forbidden."""
def initial_replay(job):
    return job.get('fresh_initial_replay')=='v60-fresh-founders-v1'
def verify(job):
    raise ValueError('V60 cannot import earlier execution results')
def seed(job):
    raise ValueError('V60 requires newly constructed founders')
