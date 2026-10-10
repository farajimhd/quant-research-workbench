from types import SimpleNamespace
import pytest
from research.vectorized_backtest.v6.torch_backtest.wall_trace import WallTrace, union_seconds


def test_overlapping_parallel_work_is_not_elapsed_wall():
    assert union_seconds([(0, 4), (1, 3), (3, 7), (9, 10)]) == 8
    with pytest.raises(ValueError):
        union_seconds([(3, 2)])


def test_nested_exclusive_times_and_restoration():
    trace = WallTrace()
    trace.events = [dict(name='parent',start=0,end=10,parent=None),
                    dict(name='child',start=2,end=5,parent=0),
                    dict(name='child',start=4,end=8,parent=0)]
    result = trace.summary()
    assert result['parent']['exclusive_work_seconds'] == 4
    assert result['child']['inclusive_work_seconds'] == 7
    assert result['child']['wall_union_seconds'] == 6
    owner = SimpleNamespace(call=lambda: 7)
    original = owner.call
    trace = WallTrace()
    with trace.patches([(owner, 'call', 'call')]):
        assert owner.call() == 7
    assert owner.call is original
    assert trace.summary()['call']['calls'] == 1


def test_failure_is_recorded_without_swallowing():
    trace = WallTrace()
    with pytest.raises(RuntimeError):
        with trace.span('failed'):
            raise RuntimeError('preserve original exception')
    assert trace.events[0]['failed']
    assert trace.summary()['failed']['calls'] == 1
