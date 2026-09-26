"""Strategy 1 launch certifies all producer seals before journal publication."""
from types import SimpleNamespace

import pytest

from src.backend import backtest_strategy_one_plan as subject
from src.backend.backtest_liquidity_price import PriceLevelPlan
from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval
from src.backend.backtest_strategy_one_activation import CertifiedActivationPlan
from src.backend.backtest_strategy_one_candidate_store import CertifiedCandidatePlan
from src.backend.backtest_strategy_one_entry_store import CertifiedEntryEvidencePlan
from src.backend.backtest_strategy_one_hod_store import CertifiedHodPlan
from src.backend.backtest_strategy_one_pivot_store import CertifiedPivotPlan
from src.backend.structural_v7_seed import CertifiedSeedPlan


def test_full_session_seals_are_checked_before_a_launch_bundle(monkeypatch):
    market = CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build", "d" * 64,
        ("2026-08-18",), ("AAA",), (), (100,), "m" * 64)
    prices = PriceLevelPlan("build", (), "p" * 64)
    candidate = CertifiedCandidatePlan(
        "build", "r" * 64, "s" * 64, (),
        (SimpleNamespace(ticker="AAA"),), "c" * 64)
    pivot = CertifiedPivotPlan("build", "2026-08-18", (), (), "i" * 64)
    activation = CertifiedActivationPlan((), "a" * 64)
    seeds = CertifiedSeedPlan("build", "v" * 64, (), "z" * 64, True)
    hod = CertifiedHodPlan("build", "2026-08-18", (), "h" * 64)
    entry = CertifiedEntryEvidencePlan(
        "build", "2026-08-18", (), (), (), "e" * 64)
    pins = {
        "token": market.token, "price_level_plan_token": prices.token,
        "strategy_one_candidate_token": candidate.token,
        "strategy_one_candidate_rule_digest": candidate.candidate_rule_digest,
        "strategy_one_scan_query_sha256": candidate.scan_query_sha256,
        "strategy_one_pivot_token": pivot.token,
        "strategy_one_activation_token": activation.token,
        "strategy_one_hod_token": hod.token,
        "strategy_one_entry_token": entry.token,
    }
    v7 = {"token": seeds.token, "catalog_hash": seeds.catalog_hash,
          "provisional": seeds.provisional}
    calls = []
    reader = SimpleNamespace(close=lambda: calls.append("closed"))
    monkeypatch.setattr(subject, "certify_candidate_plan",
                        lambda *_args, **_kwargs: candidate)
    monkeypatch.setattr(subject, "strategy_one_v7_tickers",
                        lambda _prepared: ("AAA",))
    monkeypatch.setattr(subject, "project_market_day_plan",
                        lambda _market, selected: market if selected == ("AAA",) else None)
    monkeypatch.setattr(PriceLevelPlan, "projected", lambda self, _: self)
    monkeypatch.setattr(subject, "certify_pivot_plan",
                        lambda *_args, **_kwargs: pivot)
    monkeypatch.setattr(subject, "load_strategy_one_activations",
                        lambda *_args, **_kwargs: activation)
    monkeypatch.setattr(subject, "certified_seed_plan",
                        lambda *_args: seeds)
    monkeypatch.setattr(subject, "certify_hod_plan",
                        lambda *_args, **_kwargs: hod)
    monkeypatch.setattr(subject, "certify_entry_evidence_plan",
                        lambda *_args, **_kwargs: entry)

    plan = subject.certify_strategy_one_fixed_plans(
        market, prices, market_pins=pins, v7_pins=v7,
        client_factory=lambda: reader)
    assert plan.entry is entry and plan.prices is prices
    assert calls == ["closed"]

    calls.clear()
    with pytest.raises(ValueError, match="entry evidence seal changed"):
        subject.certify_strategy_one_fixed_plans(
            market, prices,
            market_pins={**pins, "strategy_one_entry_token": "wrong"},
            v7_pins=v7, client_factory=lambda: reader)
    assert calls == ["closed"]
