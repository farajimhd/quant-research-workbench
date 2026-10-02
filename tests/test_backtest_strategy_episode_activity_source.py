from dataclasses import replace
from uuid import UUID, NAMESPACE_URL, uuid5

import pytest

from test_backtest_strategy_entry_activity_source import source_authority, ActivityBars
from src.backend.backtest_strategy_entry_activity_source import load_entry_activity_plan
from src.backend.backtest_strategy_episode_activity_gate import compile_episode_activity_static_gate
from src.backend.backtest_strategy_episode_activity_source import EpisodeActivityReadbackAuthority


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
