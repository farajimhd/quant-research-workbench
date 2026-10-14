"""Measure the actual dependency executor without throughput assumptions."""
from threading import Barrier

from pipelines.certified_research.execution import Unit,execute


def test_parallel_consumers_measure_execution_reservations_and_reuse():
    barrier=Barrier(2)
    def handler(unit,parents,stop):
        if unit.product=='consumer':barrier.wait(timeout=5)
        return {'output_hash':unit.key}
    units=(Unit('bars','bars',memory_bytes=4),
        Unit('levels','consumer',('bars',),3),Unit('indicators','consumer',('bars',),5),
        Unit('old','prior',memory_bytes=1))
    result=execute(units,{p:handler for p in ('bars','consumer','prior')},workers=2,memory_bytes=8,
        reuse=lambda unit,parents:{'output_hash':'old'} if unit.key=='old' else None)
    assert result['status']=='complete'
    measured=result['measurements']
    assert measured['peak_active_workers']==2
    assert measured['peak_reserved_memory_bytes']==8
    assert measured['memory_is_reservation'] is True
    assert measured['products']['consumer']['executed']==2
    assert measured['products']['prior']['reused']==1
    assert measured['products']['prior']['executed']==0
    assert measured['elapsed_seconds']>=0
    for product in measured['products'].values():
        assert product['handler_seconds']>=product['longest_handler_seconds']>=0
        assert product['reuse_check_seconds']>=0


def test_failures_are_measured_and_do_not_execute_descendants():
    error=ValueError('producer failed')
    def handler(unit,parents,stop):raise error
    units=(Unit('bad','bars'),Unit('child','levels',('bad',)))
    result=execute(units,{'bars':handler,'levels':handler})
    assert result['errors']['bad'].error_type=='builtins.ValueError'
    assert 'producer failed' in str(result['errors']['bad'])
    assert result['units']['child']=='blocked'
    assert result['measurements']['products']['bars']['executed']==1
    assert result['measurements']['products']['bars']['failed']==1
    assert result['measurements']['products']['levels']['executed']==0


def test_failed_reuse_is_separate_from_executed_producer():
    def reuse(unit,parents):raise ValueError('changed receipt')
    result=execute((Unit('old','bars'),),{'bars':lambda *args:None},reuse=reuse)
    product=result['measurements']['products']['bars']
    assert product['reuse_failed']==1
    assert product['executed']==product['failed']==product['reused']==0
    assert result['measurements']['peak_active_workers']==0
