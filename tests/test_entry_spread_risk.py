from dataclasses import replace
from decimal import Decimal
from datetime import date
import json
import re
import numpy as np
import pytest

from src.trading_runtime.entry_spread_risk import EntrySpreadRiskPolicy, canonical_price_int, entry_spread_risk_mask
from src.backend.backtest_entry_spread_risk import (
    load_entry_spread_risk_plan, compile_entry_spread_risk_gate, EntrySpreadRiskReadbackAuthority)
from test_backtest_strategy_episode_activity_source import authority


@pytest.mark.parametrize('ratio', [(1, 4), (1, 2)])
def test_exact_full_spread_boundary_and_decimal_grid(ratio):
    policy = EntrySpreadRiskPolicy('test@1', ratio)
    denominator = ratio[1]
    ask = 20000
    stop = ask - 400
    width = 400 // denominator
    assert entry_spread_risk_mask(policy, np.array([ask-width, ask-width-1]),
        np.array([ask,ask]), np.array([stop,stop])).tolist() == [True, False]
    assert canonical_price_int(Decimal('2.2200')) == 22200
    with pytest.raises(ValueError):
        canonical_price_int(Decimal('2.22001'))
    with pytest.raises(ValueError):
        entry_spread_risk_mask(policy, np.array([True]), np.array([2]), np.array([1]))


class Quotes:
    def __init__(self, market, *, mode='normal', width=100):
        self.market, self.mode, self.width, self.queries = market, mode, width, []

    def execute(self, sql):
        from src.backend.backtest_market_data import market_day_boundary, SESSION_OPEN_OFFSET_MS
        self.queries.append(sql)
        keys = re.findall(r"\('([^']+)',(\d+),toUUID\('([^']+)'\)\)", sql)
        rows = []
        for ticker,bucket,attempt in keys:
            boundary = (int(bucket)+1)*100-SESSION_OPEN_OFFSET_MS
            now = int(market_day_boundary(date.fromisoformat(self.market.sessions[0]),boundary).timestamp()*1_000_000)
            rows.append(dict(ticker=ticker,bucket_index=int(bucket),attempt_id=attempt,
                bid_int=100100-self.width,ask_int=100100,quote_timestamp_us=now,quote_valid=1))
        if self.mode == 'missing': rows = rows[:-1]
        if self.mode == 'duplicate': rows += rows[:1]
        if self.mode == 'future': rows[0]['quote_timestamp_us'] += 1
        if self.mode == 'unavailable': rows[0]['quote_valid'] = 0
        if self.mode == 'stale': rows[0]['quote_timestamp_us'] -= 1_000_001
        if self.mode == 'foreign': rows[0]['attempt_id'] = '00000000-0000-0000-0000-000000000002'
        return '\n'.join(map(json.dumps,rows))


def cost_source(monkeypatch, *, number=53, mode='normal', width=100):
    from src.backend import backtest_entry_spread_risk as module
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    episode = authority(number=number)
    market = replace(episode.plan.market, units=(*episode.plan.market.units,
        replace(episode.plan.market.units[0],stage='broker_100ms')))
    from src.backend.backtest_strategy_episode_activity_gate import compile_episode_activity_static_gate
    episode = replace(episode,gate=compile_episode_activity_static_gate(replace(episode.plan,market=market)))
    # Fixture market metadata is independently constructed by the existing
    # certified activity tests. This replaces only the external catalog call.
    monkeypatch.setattr(module, 'verify_market_day_plan', lambda market, client: None)
    client = Quotes(market, mode=mode, width=width)
    plan = load_entry_spread_risk_plan(market, episode.gate,
        numbered_fixed_strategy(number).entry_spread_risk_policy,client=client,batch_size=1)
    return episode, EntrySpreadRiskReadbackAuthority(episode.run_id,plan,number), client


@pytest.mark.parametrize('number', [53,54])
def test_certified_quote_reduction_preserves_full_original_anchors(monkeypatch,number):
    episode,source,client = cost_source(monkeypatch,number=number)
    gate = compile_entry_spread_risk_gate(source.plan)
    assert gate.facts == episode.gate.facts
    assert gate.eligible_indices.tolist() == episode.gate.eligible_indices.tolist()
    assert len(client.queries) == 2
    assert all('liquidity_100ms_v1' in sql and source.plan.market.build_id in sql for sql in client.queries)
    with pytest.raises(ValueError): source.plan.ask_int.setflags(write=True)
    with pytest.raises(ValueError,match='content seal'): replace(source.plan,token='f'*64)
    with pytest.raises(ValueError): replace(source,strategy_number=54 if number==53 else 53)


@pytest.mark.parametrize('mode', ['missing','duplicate','foreign','future'])
def test_missing_foreign_or_unavailable_quotes_fail_closed(monkeypatch,mode):
    with pytest.raises(ValueError): cost_source(monkeypatch,mode=mode)


def test_cost_rejection_keeps_native_first_anchor_and_reason(monkeypatch):
    episode,source,_ = cost_source(monkeypatch,width=301)
    gate = compile_entry_spread_risk_gate(source.plan)
    assert gate.facts == episode.gate.facts
    assert gate.eligible_indices.size == 0
    assert gate.rejection_mask.tolist() == [1<<11,1<<11]
    with pytest.raises(ValueError,match='cap rejected'): source.witness('AAA',41000)

@pytest.mark.parametrize('mode', ['stale','unavailable'])
def test_covered_explicit_quote_unavailability_rejects_only_candidate(monkeypatch, mode):
    episode, source, client = cost_source(monkeypatch, mode=mode)
    gate = compile_entry_spread_risk_gate(source.plan)
    assert gate.rejection_mask.tolist() == [1<<12,1<<12]
    assert gate.facts == episode.gate.facts
    with pytest.raises(ValueError,match='unavailable or stale'):source.witness('AAA',41000)
