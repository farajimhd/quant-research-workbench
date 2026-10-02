from dataclasses import replace
from uuid import UUID, NAMESPACE_URL, uuid5

import pytest

from test_backtest_strategy_entry_activity_source import source_authority, ActivityBars
from src.backend.backtest_strategy_entry_activity_source import load_entry_activity_plan
from src.backend.backtest_strategy_episode_activity_gate import compile_episode_activity_static_gate
from src.backend.backtest_strategy_episode_activity_source import (
    EpisodeActivityReadbackAuthority, certified_episode_activity_witness,
    bind_episode_activity_proposal, certified_episode_entry_intent,
)


def authority(mode='normal', ten_percent=True):
    market, parent = source_authority(ten_percent=ten_percent)
    activity = load_entry_activity_plan(market, parent, client=ActivityBars(mode))
    return EpisodeActivityReadbackAuthority('episode-run', compile_episode_activity_static_gate(activity))


def test_prefix_token_binds_native_candle_proof_without_changing_observations():
    source = authority()
    ticker = source.gate.facts[1].ticker
    witness = source.witness(ticker, 41000)
    native = source.plan.witness(ticker, 41000)
    assert witness.activity_source_token == source.gate.token != native.activity_source_token
    assert replace(witness, activity_source_token=native.activity_source_token) == native
    with pytest.raises(ValueError):
        authority(ten_percent=False)


def test_cold_source_does_not_accept_later_recovery_in_vetoed_episode():
    source = authority('fade')
    with pytest.raises(ValueError, match='admitted causal prefix'):
        source.witness(source.gate.facts[1].ticker, 41000)


def test_readback_uses_exact_number_run_and_original_episode_identity():
    source = authority()
    witness = source.witness(source.gate.facts[1].ticker, 41000)
    parent = str(UUID(int=3737))
    entry = dict(strategy_number=37,parent_record_id=parent,run_id='episode-run',
        batch_id=str(UUID(int=37)),event_month='2026-08-01',boundary_ms=41000,
        episode_start_ms=30000,assignment_id='assignment')
    identity = f'strategy-37:{witness.session_date}:assignment:account:{witness.ticker}:41000:30000'
    intent = dict(record_id=parent,run_id=entry['run_id'],batch_id=entry['batch_id'],
        event_month=entry['event_month'],ticker=witness.ticker,account_id='account',
        action='enter_long',reason='strategy_one_entry',intent_id=str(uuid5(NAMESPACE_URL,identity)))
    result = source.resolve('episode-run', (entry,), (intent,))
    assert result[0].witness == witness and result[0].episode_start_ms == 30000
    for changed in (dict(entry,strategy_number=36),dict(entry,episode_start_ms=30100)):
        with pytest.raises(ValueError):
            source.resolve('episode-run',(changed,),(intent,))
    with pytest.raises(ValueError, match='certified run'):
        source.resolve('another-run',(entry,),(intent,))
    with pytest.raises(ValueError, match='duplicate'):
        source.resolve('episode-run',(entry,),(intent,intent))


def test_cached_manager_guard_rejects_wrong_run_number_episode_and_prefix():
    from datetime import date
    from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority
    from src.backend.backtest_strategy_entry_activity_source import EntryActivityReadbackAuthority
    from test_strategy_one_intent import _proposal
    source = authority()
    parent = CertifiedPriceReadbackAuthority(source.run_id,source.plan.parent,source)
    proposal = replace(_proposal(),strategy_number=37,ticker='AAA',boundary_ms=41000,
                       episode_start_ms=30000)
    day = date.fromisoformat(source.plan.market.sessions[0])
    assert certified_episode_activity_witness(parent,proposal,session_date=day) == source.witness('AAA',41000)
    for changed in (replace(proposal,strategy_number=36),replace(proposal,episode_start_ms=30100)):
        with pytest.raises(ValueError):
            certified_episode_activity_witness(parent,changed,session_date=day)
    legacy = CertifiedPriceReadbackAuthority(source.run_id,source.plan.parent,
        EntryActivityReadbackAuthority(source.run_id,source.plan))
    with pytest.raises(ValueError,match='exact certified'):
        certified_episode_activity_witness(legacy,proposal,session_date=day)
    blocked = authority('fade')
    with pytest.raises(ValueError,match='admitted causal prefix'):
        certified_episode_activity_witness(CertifiedPriceReadbackAuthority(blocked.run_id,
            blocked.plan.parent,blocked),proposal,session_date=day)


def test_complete_parent_entry_binding_changes_only_number_and_deterministic_identity():
    from datetime import date
    from src.backend.backtest_strategy_certified_price_break import (
        CertifiedPriceReadbackAuthority, bind_certified_price_break_proposal,
        certified_price_entry_intent,
    )
    from test_strategy_one_intent import _proposal
    source = authority()
    context = CertifiedPriceReadbackAuthority(source.run_id,source.plan.parent,source)
    day = date.fromisoformat(source.plan.market.sessions[0])
    original = replace(_proposal(),strategy_number=18,boundary_ms=41000,
        momentum=source.plan.parent.momentum.lookup('AAA',41000),
        initial_momentum=source.plan.parent.source.parent.selection_witness('AAA',41000))
    parent = bind_certified_price_break_proposal(source.plan.parent,original,strategy_number=36)
    proposal = bind_episode_activity_proposal(context,parent,session_date=day)
    assert replace(proposal,strategy_number=36) == parent
    parent_intent = certified_price_entry_intent(context.plan,parent,session_date=day)
    intent = certified_episode_entry_intent(context,proposal,session_date=day)
    assert intent.intent_id != parent_intent.intent_id
    assert replace(intent,intent_id=parent_intent.intent_id) == parent_intent
    with pytest.raises(ValueError):
        certified_episode_entry_intent(context,replace(proposal,reference_ask=0),session_date=day)
    blocked = authority('fade')
    blocked_original = replace(_proposal(),strategy_number=18,boundary_ms=41000,
        momentum=blocked.plan.parent.momentum.lookup('AAA',41000),
        initial_momentum=blocked.plan.parent.source.parent.selection_witness('AAA',41000))
    blocked_parent = bind_certified_price_break_proposal(blocked.plan.parent,blocked_original,
        strategy_number=36)
    with pytest.raises(ValueError,match='admitted causal prefix'):
        bind_episode_activity_proposal(CertifiedPriceReadbackAuthority(blocked.run_id,
            blocked.plan.parent,blocked),blocked_parent,session_date=day)
