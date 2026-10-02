from dataclasses import replace
import re

import numpy as np
import pyarrow as pa
import pytest

from test_backtest_strategy_first_price_source import authority, Bars
from src.backend.backtest_strategy_first_price_source import load_first_price_source
from src.backend.backtest_strategy_certified_price_break import compile_certified_price_break_plan
from src.backend.backtest_strategy_entry_activity_source import load_entry_activity_plan


def source_authority():
    market, parent = authority()
    market = replace(market, required_resolutions_ms=tuple(sorted(set(market.required_resolutions_ms) | {5000})))
    return market, compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))


class ActivityBars:
    def __init__(self, mode='normal'):
        self.mode = mode
        self.queries = []
        self.closed = False

    def iter_arrow_record_batches(self, sql):
        self.queries.append(sql)
        buckets = list(map(int, re.search(r'bucket_index IN \(([^)]+)\)', sql).group(1).split(',')))
        if self.mode == 'missing':
            buckets = buckets[-1:]
        if self.mode == 'empty':
            buckets = []
        if self.mode == 'duplicate':
            buckets = [buckets[0], buckets[0]]
        if self.mode == 'outside':
            buckets[0] -= 1
        counts = [10 if self.mode == 'fade' and (b + 1) * 5000 - 14400000 >= 25000
                  else 0 if self.mode == 'zero' else 100 for b in buckets]
        try:
            yield pa.RecordBatch.from_arrays([
                pa.array(buckets, type=pa.uint32()),
                pa.array([10000 if self.mode == 'wrong_resolution' else 5000] * len(buckets), type=pa.uint32()),
                pa.array(counts, type=pa.uint64()),
            ], names=['bucket_index', 'resolution_ms', 'trade_count'])
        finally:
            self.closed = True


def test_native_projection_scope_and_seal():
    market, parent = source_authority()
    client = ActivityBars()
    plan = load_entry_activity_plan(market, parent, client=client)
    assert plan.eligible_mask.tolist() == [True, True]
    assert plan.requested_mask.tolist() == [True, True]
    assert plan.candle_boundaries_ms.tolist() == [[15000, 20000, 25000, 30000], [25000, 30000, 35000, 40000]]
    assert client.closed and len(client.queries) == 1
    assert parent.candidates.coverage[0].source_attempts[0] in client.queries[0]
    assert 'SELECT bucket_index,resolution_ms,trade_count' in client.queries[0]
    assert 'resolution_ms=5000' in client.queries[0]
    with pytest.raises(ValueError):
        plan.trade_counts.setflags(write=True)
    with pytest.raises(ValueError, match='content seal'):
        replace(plan, token='f' * 64)
    with pytest.raises(ValueError, match='eligibility'):
        replace(plan, eligible_mask=np.asarray([False, True]))
    changed_counts = plan.trade_counts.copy()
    changed_counts[0, 0] += np.uint64(1)
    with pytest.raises(ValueError, match='content seal'):
        replace(plan, trade_counts=changed_counts)
    changed_clocks = plan.candle_boundaries_ms.copy()
    changed_clocks[0, 0] += 5000
    with pytest.raises(ValueError, match='observed clock'):
        replace(plan, candle_boundaries_ms=changed_clocks)


def test_faded_entry_does_not_rewrite_original_activation_or_later_candidate():
    market, parent = source_authority()
    plan = load_entry_activity_plan(market, parent, client=ActivityBars('fade'))
    assert plan.eligible_mask.tolist() == [False, True]
    assert parent.eligible_mask.tolist() == [True, True]
    assert plan.parent.entry is parent.entry
    assert plan.parent.source.parent.initial.first_indices.tolist() == [0, 0]


@pytest.mark.parametrize('mode', ['missing', 'empty'])
def test_missing_source_is_unknown_not_zero(mode):
    market, parent = source_authority()
    plan = load_entry_activity_plan(market, parent, client=ActivityBars(mode))
    assert not np.any(plan.eligible_mask)
    assert np.all(plan.trade_counts[~plan.observed] == 0)
    assert np.all(plan.candle_boundaries_ms[~plan.observed] == -1)


def test_real_zero_counts_are_present():
    market, parent = source_authority()
    plan = load_entry_activity_plan(market, parent, client=ActivityBars('zero'))
    assert plan.observed.all() and plan.eligible_mask.all()


@pytest.mark.parametrize('mode', ['duplicate', 'outside', 'wrong_resolution'])
def test_malformed_native_rows_reject_and_close_stream(mode):
    market, parent = source_authority()
    client = ActivityBars(mode)
    with pytest.raises(ValueError):
        load_entry_activity_plan(market, parent, client=client)
    assert client.closed


def test_source_attempt_mismatch_fails_before_read():
    market, parent = source_authority()
    client = ActivityBars()
    wrong = replace(market, units=tuple(replace(u, attempt_id='a' * 36) if u.stage == 'bars' else u
                                        for u in market.units))
    with pytest.raises(ValueError, match='bars attempt'):
        load_entry_activity_plan(wrong, parent, client=client)
    assert not client.queries
