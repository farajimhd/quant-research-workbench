from dataclasses import replace
import re

import numpy as np
import pyarrow as pa
import pytest

from test_backtest_strategy_initial_price_break import parent_plan
from test_backtest_strategy_initial_momentum_growth import market_for
from src.backend.backtest_strategy_first_price_source import load_first_price_source
from src.backend.backtest_strategy_initial_price_break import stage_initial_price_break_plan


def authority():
    parent = parent_plan()
    market = market_for(parent.candidates)
    bars = replace(market.units[0], stage='bars',
                   attempt_id=parent.candidates.coverage[0].source_attempts[0])
    return replace(market, units=(*market.units, bars)), parent


class Bars:
    def __init__(self, mode='normal'):
        self.mode = mode
        self.queries = []

    def iter_arrow_record_batches(self, sql):
        self.queries.append(sql)
        buckets = list(map(int, re.search(r'bucket_index IN \(([^)]+)\)', sql).group(1).split(',')))
        if self.mode == 'missing':
            buckets = buckets[-1:]
        if self.mode == 'duplicate':
            buckets = [buckets[0], buckets[0]]
        if self.mode == 'outside':
            buckets[0] -= 1
        yield pa.RecordBatch.from_arrays([
            pa.array(buckets, type=pa.uint32()),
            pa.array([101] * len(buckets), type=pa.uint64()),
            pa.array([100] * len(buckets), type=pa.uint64()),
            pa.array([2 if self.mode == 'flags' else 1] * len(buckets), type=pa.uint8()),
            pa.array([1] * len(buckets), type=pa.uint8()),
        ], names=['bucket_index', 'close_int', 'high_int', 'price_valid', 'extremes_valid'])


def test_sparse_projection_pins_original_first_and_bars_attempt():
    market, parent = authority()
    client = Bars()
    source = load_first_price_source(market, parent, client=client)
    assert source.requested_mask.tolist() == [True, False]
    assert source.observations[0].tolist() == [31000, 0]
    assert source.observations[1].tolist() == [30000, 0]
    assert len(client.queries) == 1
    assert parent.candidates.coverage[0].source_attempts[0] in client.queries[0]
    assert stage_initial_price_break_plan(parent, source.observations).eligible_mask.tolist() == [True, True]
    with pytest.raises(ValueError):
        source.observations[2].setflags(write=True)
    with pytest.raises(ValueError, match='content seal'):
        replace(source, token='f' * 64)


def test_missing_prior_rejects_without_carry():
    market, parent = authority()
    source = load_first_price_source(market, parent, client=Bars('missing'))
    assert source.observations[1].tolist() == [0, 0]
    assert not np.any(stage_initial_price_break_plan(parent, source.observations).eligible_mask)


@pytest.mark.parametrize('mode', ['duplicate', 'outside', 'flags'])
def test_malformed_source_rejected(mode):
    market, parent = authority()
    with pytest.raises(ValueError):
        load_first_price_source(market, parent, client=Bars(mode))


def test_wrong_bars_attempt_rejected_before_read():
    market, parent = authority()
    client = Bars()
    wrong = replace(market, units=(market.units[0], replace(market.units[1], attempt_id=market.units[0].attempt_id)))
    with pytest.raises(ValueError, match='bars attempt'):
        load_first_price_source(wrong, parent, client=client)
    assert not client.queries


def test_no_parent_admitted_rows_requires_no_price_reads():
    from test_backtest_strategy_initial_momentum import plans
    from test_backtest_strategy_initial_momentum_growth import StrongLater
    from src.backend.backtest_strategy_rising_momentum import load_rising_momentum_plan
    from src.backend.backtest_strategy_initial_momentum_growth import compile_initial_momentum_growth_plan
    candidates, entry, _, _ = plans()
    market = market_for(candidates)
    momentum = load_rising_momentum_plan(market, candidates, client=StrongLater())
    parent = compile_initial_momentum_growth_plan(candidates, entry, momentum)
    bars = replace(market.units[0], stage='bars', attempt_id=candidates.coverage[0].source_attempts[0])
    market = replace(market, units=(*market.units, bars))
    client = Bars()
    source = load_first_price_source(market, parent, client=client)
    assert not np.any(source.requested_mask)
    assert not client.queries
