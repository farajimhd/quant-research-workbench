import numpy as np
import pytest
import torch

from research.vectorized_backtest.v7.qualify import synthetic, member
from research.vectorized_backtest.v19.evaluator import Execution
from research.vectorized_backtest.v19.audit_reference import trace_reference_session


def data():
    item = synthetic('cpu')
    item.arrays = {'clocks': np.arange(1790000000, 1790000006, dtype=np.int64)}
    item.receipt = {'listings': [{'listing_id': 'certified-listing'}]}
    return item


def test_source_trace_matches_original_replay_and_preserves_clock_lag():
    item = data()
    gates = torch.tensor([1, 4, 8, 0, 0, 0], dtype=torch.uint8)[None, :, None]
    result = trace_reference_session(item, member(), Execution(fee_per_share=.005, minimum_order_fee=1.),
        gates=gates, max_rows=5)
    trace = result['trace']
    assert trace.height == 5
    assert trace['listing_id'].unique().to_list() == ['certified-listing']
    assert (trace['execution_at'][0] - trace['signal_at'][0]).total_seconds() == 1
    assert trace['transition'][0] == 'entry' and trace['transition'][-1] == 'exit'
    assert trace['after_quantity'][-1] == 0
    assert trace['after_net_realized_pnl'][-1] == result['metrics']['net_pnl'][0].item()
    assert trace['modeled_commission'].sum() >= 2
    assert 'no OMS' in result['unavailable']['orders']


def test_reference_trace_honors_observed_membership_and_bounds():
    item = data()
    item.tensors['membership'][:] = False
    gates = torch.ones((1, 6, 1), dtype=torch.uint8)
    result = trace_reference_session(item, member(), Execution(), gates=gates, max_rows=5)
    assert result['trace']['after_quantity'].sum() == 0
    with pytest.raises(ValueError, match='row bound'):
        trace_reference_session(item, member(), Execution(), gates=gates, max_rows=4)


def test_source_stateful_bits_and_age_mask_remain_distinct():
    from dataclasses import replace
    candidate = replace(member(), minimum_age={'add': 100})
    gates = torch.tensor([1, 4 << 8, 4 << 8, 0, 0, 0], dtype=torch.int16)[None, :, None]
    result = trace_reference_session(data(), candidate, Execution(), gates=gates, max_rows=5)
    assert result['trace']['raw_gate_bits'][1] == 4 << 8
    assert result['trace']['selected_gate_bits'][1] == 0
