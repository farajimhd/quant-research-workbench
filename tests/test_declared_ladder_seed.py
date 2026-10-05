"""Exact prior NYSE checkpoint admission, including certified empty states."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.backend.backtest_declared_ladder_seed import verify_declared_ladder_seed_plan
from src.backend.structural_v7_seed import CertifiedSeedPlan
from src.market_engine.derived_trade_policy import POLICY


def fixture(*, target='2026-09-08', prior='2026-09-04', empty=False):
    market = SimpleNamespace(build_id='build', units=(SimpleNamespace(
        stage='bars', session_date=target, ticker='TEST'),))
    unit = dict(ticker='TEST', backtest_session=target, session_date=prior,
        available_at=prior+' 23:59:59.000000000', source_checkpoint_hash='a'*64,
        source_plan_hash='b'*64, level_count=0 if empty else 1,
        observation_count=0 if empty else 2, input_policy='' if empty else POLICY)
    return market, CertifiedSeedPlan('build', 'b'*64, (unit,), 'c'*64, False)


@pytest.mark.parametrize('empty', [True, False])
def test_exact_holiday_prior_checkpoint_is_admitted_without_substitution(empty):
    market, seeds = fixture(empty=empty)
    assert verify_declared_ladder_seed_plan(market, seeds) is seeds


@pytest.mark.parametrize('field,value', [
    ('session_date','2026-09-03'), ('source_checkpoint_hash','0'*64),
    ('source_plan_hash',''), ('source_checkpoint_hash','G'*64),
    ('input_policy','legacy-unfiltered'), ('level_count',-1),
    ('backtest_session','2026-09-09'), ('ticker','OTHER'),
    ('available_at','2026-09-08 08:00:00.000000001'), ('available_at','NaT')])
def test_stale_noncanonical_or_unavailable_seed_is_rejected(field, value):
    market, seeds = fixture()
    seeds = replace(seeds, units=({**seeds.units[0],field:value},))
    with pytest.raises(ValueError):
        verify_declared_ladder_seed_plan(market, seeds)


@pytest.mark.parametrize('mutation', ['provisional','missing','duplicate','build'])
def test_plan_scope_and_provisional_seed_rejected(mutation):
    market, seeds = fixture()
    if mutation == 'provisional':
        seeds = replace(seeds, provisional=True)
    elif mutation == 'missing':
        seeds = replace(seeds, units=())
    elif mutation == 'duplicate':
        seeds = replace(seeds, units=seeds.units*2)
    else:
        seeds = replace(seeds, build_id='wrong')
    with pytest.raises(ValueError):
        verify_declared_ladder_seed_plan(market, seeds)


def test_uncertified_empty_or_empty_legacy_policy_is_rejected():
    market, seeds = fixture(empty=True)
    for changes in ({'source_checkpoint_hash':'0'*64}, {'input_policy':'legacy-unfiltered'}):
        with pytest.raises(ValueError):
            verify_declared_ladder_seed_plan(market,
                replace(seeds, units=({**seeds.units[0],**changes},)))


def test_closed_target_day_rejected():
    market, seeds = fixture(target='2026-09-07')
    with pytest.raises(ValueError, match='NYSE session'):
        verify_declared_ladder_seed_plan(market, seeds)
