import gc
import weakref

from pipelines.certified_research.execution import Unit,execute,FailureSnapshot


class Payload:
    def __init__(self):self.data=bytearray(1024**2)


def test_retained_results_do_not_retain_failed_worker_payloads_or_chains():
    refs=[]
    def handler(unit,parents,stop):
        payload=Payload();refs.append(weakref.ref(payload))
        try:raise ValueError('source failed')
        except ValueError as cause:
            failure=RuntimeError('publication failed');failure.payload=payload
            raise failure from cause
    result=execute(tuple(Unit(str(n),'source',memory_bytes=1) for n in range(8)),
        {'source':handler},workers=2,memory_bytes=2)
    assert result['status']=='failed' and len(result['errors'])==8
    gc.collect()
    assert all(reference() is None for reference in refs)
    for error in result['errors'].values():
        assert isinstance(error,FailureSnapshot) and error.__traceback__ is None
        assert error.__cause__ is None and error.__context__ is None
        assert error.error_type=='builtins.RuntimeError' and error.location is not None
        assert error.causes==( ('builtins.ValueError','source failed',False), )


def test_reuse_and_missing_receipt_failures_also_use_bounded_snapshots():
    def reuse(unit,parents):raise ValueError('x'*10000)
    result=execute((Unit('old','source'),),{'source':lambda *args:None},reuse=reuse)
    error=result['errors']['old']
    assert error.message_truncated and error.original_message_characters==10000
    assert len(str(error))<2200 and error.__traceback__ is None
    empty=execute((Unit('new','source'),),{'source':lambda *args:None})
    assert empty['errors']['new'].__traceback__ is None
