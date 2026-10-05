from dataclasses import replace
import json
import pytest
from test_entry_spread_risk import Quotes
from test_backtest_strategy_episode_activity_source import authority
from src.backend.backtest_entry_spread_risk import compile_entry_spread_risk_gate

class V2Quotes(Quotes):
    def execute(self, sql):
        assert 'toString(l.attempt_id) AS liquidity_attempt_id' in sql
        assert '(l.ticker,l.bucket_index,l.attempt_id) IN' in sql
        assert 'FROM arte.liquidity_100ms_v1 AS l' in sql
        result = super().execute(sql)
        rows = [json.loads(line) for line in result.splitlines()]
        for row in rows:
            row['liquidity_attempt_id'] = row.pop('attempt_id')
        return '\n'.join(map(json.dumps,rows))

def cost_source(monkeypatch, *, number=55, mode='normal', width=100):
    from src.backend import backtest_entry_spread_risk_v2 as module
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    episode = authority(number=number)
    market = replace(episode.plan.market, units=(*episode.plan.market.units,
        replace(episode.plan.market.units[0],stage='broker_100ms')))
    from src.backend.backtest_strategy_episode_activity_gate import compile_episode_activity_static_gate
    episode = replace(episode,gate=compile_episode_activity_static_gate(replace(episode.plan,market=market)))
    # Fixture market metadata is independently constructed by the existing
    # certified activity tests. This replaces only the external catalog call.
    monkeypatch.setattr(module, 'verify_market_day_plan', lambda market, client: None)
    client = V2Quotes(market, mode=mode, width=width)
    plan = module.load_entry_spread_risk_plan_v2(market, episode.gate,
        numbered_fixed_strategy(number).entry_spread_risk_policy,client=client,batch_size=1)
    return episode, module.EntrySpreadRiskV2ReadbackAuthority(episode.run_id,plan,number), client


@pytest.mark.parametrize('number', [55,56])
def test_certified_quote_reduction_preserves_full_original_anchors(monkeypatch,number):
    episode,source,client = cost_source(monkeypatch,number=number)
    gate = compile_entry_spread_risk_gate(source.plan)
    assert gate.facts == episode.gate.facts
    assert gate.eligible_indices.tolist() == episode.gate.eligible_indices.tolist()
    assert len(client.queries) == 2
    assert all('liquidity_100ms_v1' in sql and source.plan.market.build_id in sql for sql in client.queries)
    with pytest.raises(ValueError): source.plan.ask_int.setflags(write=True)
    with pytest.raises(ValueError,match='content seal'): replace(source.plan,token='f'*64)
    with pytest.raises(ValueError): replace(source,strategy_number=56 if number==55 else 55)


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

@pytest.mark.parametrize('new,old',[(55,53),(56,54)])
def test_v2_cannot_rebind_to_old_same_cap_source(monkeypatch,new,old):
    from src.backend.backtest_entry_spread_risk import EntrySpreadRiskReadbackAuthority
    from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority
    episode,source,_=cost_source(monkeypatch,number=new)
    with pytest.raises(ValueError,match='source lineage'):
        replace(source,strategy_number=old)
    legacy=EntrySpreadRiskReadbackAuthority(source.run_id,source.plan,new)
    with pytest.raises(ValueError):
        CertifiedPriceReadbackAuthority(episode.run_id,episode.plan.parent,episode,legacy)

@pytest.mark.parametrize('number',[55,56])
def test_generic_dispatch_is_declared_and_unknown_version_fails(monkeypatch,number):
    from src.backend.backtest_declared_entry_quote_source import load_declared_entry_spread_risk_plan, declared_entry_spread_risk_authority
    episode,source,_=cost_source(monkeypatch,number=number)
    client=V2Quotes(source.plan.market)
    plan=load_declared_entry_spread_risk_plan(source.plan.market,episode.gate,source.plan.policy,source_contract='declared-entry-spread-risk-quote-source@2',client=client)
    assert plan.token==source.plan.token
    assert type(declared_entry_spread_risk_authority(source.run_id,plan,number)) is type(source)
    with pytest.raises(ValueError):load_declared_entry_spread_risk_plan(source.plan.market,episode.gate,source.plan.policy,source_contract='unknown',client=client)
