import pytest
from research.vectorized_backtest.v63.performance import configuration
from research.vectorized_backtest.v63.profile_throughput import comparable


@pytest.mark.parametrize('workers',[2,4])
@pytest.mark.parametrize('block',[256,512,1024])
def test_bounded_execution_settings(workers,block):
    assert configuration(dict(global_session_workers=workers,replay_tuning=dict(time_block=block,transfer_mode='events')))==dict(
        session_workers=workers,time_block=block,transfer_mode='events')


@pytest.mark.parametrize('job',[
    dict(global_session_workers=8),dict(replay_tuning=dict(time_block=4096)),
    dict(replay_tuning=dict(transfer_mode='unknown')),dict(replay_tuning=dict(drop_events=True))])
def test_unknown_or_unbounded_settings_rejected(job):
    with pytest.raises(ValueError):configuration(job)


def test_order_parity_ignores_paths_but_preserves_counts_and_content():
    def metrics(path,count,sha):return dict(net_pnl=[1.],execution_orders=[dict(receipt=path,candidate_index=0,orders=count,sha256=sha)])
    baseline=comparable(metrics('a',10,'abc'))
    assert baseline==comparable(metrics('b',10,'abc'))
    assert baseline!=comparable(metrics('b',11,'abc'))
    assert baseline!=comparable(metrics('b',10,'def'))
