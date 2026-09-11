from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.trading_runtime.estimated_luld import estimate, reference_from_indicator
from src.backend.backtest_luld_reference import previous_regular_close


def observation(at, reference=10., prior=2.):
    return SimpleNamespace(observed_at=at,price=10.,previous_close=prior,
        backtest_luld_reference=dict(reference_price=reference,available_at_ms=at.timestamp()*1000))


def test_reference_is_held_then_updates_and_survives_resume():
    at=datetime(2026,8,21,14,tzinfo=UTC);state={}
    assert estimate(observation(at),state)['upper']==12
    assert estimate(observation(at+timedelta(seconds=29),11),state)['upper']==12
    resumed=deepcopy(state)
    next_obs=observation(at+timedelta(seconds=30),11)
    assert estimate(next_obs,state)==estimate(next_obs,resumed)
    assert state['reference']==11
    assert estimate(observation(at+timedelta(seconds=60),11.05),state)['reference_price']==11


@pytest.mark.parametrize('prior,width',[(.74,.15),(.75,2.),(3.,2.),(3.01,1.)])
def test_width_uses_prior_close_even_after_large_gap(prior,width):
    band=estimate(observation(datetime(2026,8,21,14,tzinfo=UTC),prior=prior),{})
    assert band['upper']==pytest.approx(10+width)


def test_opening_proxy_and_close_doubling_and_outside_regular():
    at=datetime(2026,8,21,13,30,tzinfo=UTC);state={}
    assert estimate(observation(at,reference=8),state)['reference_price']==10
    assert estimate(observation(at+timedelta(minutes=4),reference=11),state)['reference_price']==10
    assert estimate(observation(at+timedelta(minutes=5),reference=11),state)['reference_price']==11
    assert estimate(observation(at.replace(hour=19,minute=35)),{})['upper']==14
    assert estimate(observation(at.replace(hour=20,minute=0)),{}) is None


def test_future_stale_and_invalid_evidence_rejected():
    at=datetime(2026,8,21,14,tzinfo=UTC)
    for delta in (-6000,1):
        obs=observation(at);obs.backtest_luld_reference['available_at_ms']+=delta
        assert estimate(obs,{}) is None
    assert reference_from_indicator({},at)=={}
    assert estimate(observation(at,prior=None),{}) is None


def test_qmd_bar_reference_preferred_over_old_band_midpoint():
    at=datetime(2026,8,21,14,tzinfo=UTC)
    result=reference_from_indicator(dict(qmd_structure_luld_lower=8,qmd_structure_luld_upper=12),at,
        dict(estimated_luld_active=True,estimated_luld_reference_price=11))
    assert result['reference_price']==11


def test_prior_close_excludes_after_hours_and_uses_early_close():
    payload=dict(bars=[
        dict(bar_end='2026-11-27T18:00:00Z',close=2.8,trade_count=3),
        dict(bar_end='2026-11-27T19:00:00Z',close=5.,trade_count=1)])
    with patch('src.backend.backtest_luld_reference.qmd_product_request',return_value=SimpleNamespace(payload=payload)) as query:
        result=previous_regular_close('TEST',date(2026,11,30))
    assert result['price']==2.8
    assert query.call_args.args[0].end=='2026-11-27T18:00:00+00:00'
