"""Missed-pair eligibility, journal matching and objective reference invariants."""
from types import SimpleNamespace
import numpy as np
import polars as pl
import pytest
from research.vectorized_backtest.v68.opportunity_cost import entries,captured,apply,MISSED_COST
from research.vectorized_backtest.v68.robust_score import combine


def item():
    actions=np.array([[2],[2],[3],[2],[4],[2],[4]],np.int8)
    pairs=np.array([[1],[1],[1],[2],[2],[3],[3]],np.int32)
    host={k:np.full(actions.shape,v) for k,v in dict(membership=True,quote_valid=True,bid=10.,ask=10.01,ask_size=100.).items()}
    host['ask'][3]=2000.
    return SimpleNamespace(supervision=(actions,pairs,pairs),host_tensors=host,listing_ids=np.array([1]))


def order(submit=0,fill=1,kind=1,quantity=1.):
    return [0.,float(kind),1.,quantity,10.,0.,float(submit),float(fill),6.]


def test_pair_eligibility_excludes_impossible_quote_but_preserves_entry_window():
    target=entries(item())
    assert target['pair'].unique().sort().to_list()==[1,3]
    assert target.filter(pl.col('pair')==1)['clock'].to_list()==[0,1]


def test_terminal_liquidation_clock_is_not_an_entry_opportunity():
    market=item();market.supervision[0][-1,0]=2;market.supervision[1][-1,0]=4
    assert 4 not in entries(market)['pair'].to_list()


def test_actual_aligned_fills_count_once_per_pair():
    metrics={'net_pnl':[0.,0.],'execution_orders':[[order(),order(),order(kind=2)],[]]}
    apply(metrics,item())
    assert metrics['label_opportunities']==[2,2]
    assert metrics['label_captured']==[1,0]
    assert metrics['label_missed']==[1,2]
    assert metrics['label_missed_penalty']==[MISSED_COST,2*MISSED_COST]
    with pytest.raises(ValueError,match='exactly once'):apply(metrics,item())


@pytest.mark.parametrize('record',[order(quantity=0),order(fill=5),order(submit=2),order(kind=2)])
def test_unfilled_unaligned_and_add_orders_do_not_capture(record):
    metrics={'net_pnl':[0.],'execution_orders':[[record]]}
    apply(metrics,item());assert metrics['label_captured']==[0]


@pytest.mark.parametrize('channel,value',[('membership',False),('quote_valid',False),('ask_size',0.),('bid',.01)])
def test_unavailable_opportunities_are_masked(channel,value):
    market=item();market.host_tensors[channel][:]=value
    assert entries(market).height==0


def test_absolute_cost_and_relative_fitness_reconcile_across_sessions():
    base={k:np.zeros(2) for k in ('net_pnl','gross_losses','drawdown','closed_positions','winning_positions',
          'ticker_component','blocked_requests','label_reward','label_penalty','label_entry_errors',
          'label_exit_errors','label_late_notional_seconds','maximum_position_risk')}
    base.update(eligible=np.ones(2,bool),label_opportunities=np.array([2.,2.]),
                label_captured=np.array([1.,0.]),label_missed=np.array([1.,2.]),
                label_missed_penalty=np.array([2.5,5.]),label_capture_bonus=np.array([2.5,0.]))
    result=combine([base,base])
    np.testing.assert_array_equal(result['absolute_score'],[-5.,-10.])
    np.testing.assert_array_equal(result['score'],[5.,0.])
    np.testing.assert_array_equal(result['score'],result['absolute_score']-result['abstention_score'])
    assert not result['parent_eligible'].any()


def test_handoff_preserves_per_ticker_capture_credit_in_behavior_keys():
    from research.vectorized_backtest.v68.controller_metrics import SCALARS,LEDGERS,compact
    from research.vectorized_backtest.v68.behavior_selection import session_keys
    metrics={key:[0.,0.] for key in SCALARS}
    metrics.update({key:[[],[]] for key in LEDGERS})
    metrics['label_ticker_capture_bonus']=[[2.5,0.],[0.,2.5]]
    handed=compact(metrics)
    np.testing.assert_array_equal(handed['label_ticker_capture_bonus'][0],[2.5,0.])
    assert session_keys(handed)[0]!=session_keys(handed)[1]


def test_shared_journal_deduplicates_across_pages_and_rejects_corruption(tmp_path):
    from research.vectorized_backtest.v68.order_journal import Journal
    journal=Journal(tmp_path,2,{'test':'pair-deduplication'})
    for _ in range(2):
        journal.write(0,np.array([0,1]),np.array([1,0]),np.array([order()]))
        journal.flush()
    refs=journal.finish()
    metrics={'net_pnl':[0.,0.,0.],'execution_orders':[refs[0],refs[1],refs[0]]}
    apply(metrics,item());assert metrics['label_captured']==[1,0,1]
    from pathlib import Path
    import json
    receipt=Path(refs[0]['receipt']);page=json.loads(receipt.read_text())['pages'][0]
    file=receipt.parent/page['file'];file.write_bytes(file.read_bytes()+b'corrupt')
    with pytest.raises(ValueError,match='page changed'):
        apply({'net_pnl':[0.],'execution_orders':[refs[0]]},item())


def test_cost_reads_real_journal_beyond_windows_path_limit(tmp_path):
    from research.vectorized_backtest.v68.order_journal import Journal
    root=tmp_path
    while len(str(root))<280:root=root/'long-qualification-generation-and-session'
    journal=Journal(root,1,{'test':'long-path'})
    journal.write(0,np.array([0]),np.array([1]),np.array([order()]))
    refs=journal.finish()
    metrics={'net_pnl':[0.],'execution_orders':refs}
    apply(metrics,item());assert metrics['label_captured']==[1]
