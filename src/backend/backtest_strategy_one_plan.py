"""SELECT-only launch certificate for one numbered Strategy 1 session.

All full-session producer seals are checked before opening a journal run gate.
The returned execution projection is only an in-memory read scope; it never
replaces the certified parent population or writes ARTE market products.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from src.backend.backtest_liquidity_price import PriceLevelPlan
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, project_market_day_plan,
)
from src.backend.backtest_strategy_one_activation import (
    CertifiedActivationPlan, load_strategy_one_activations,
)
from src.backend.backtest_strategy_one_candidate_store import (
    CertifiedCandidatePlan, certify_candidate_plan,
)
from src.backend.backtest_strategy_one_entry_store import (
    CertifiedEntryEvidencePlan, certify_entry_evidence_plan,
)
from src.backend.backtest_strategy_one_hod_store import (
    CertifiedHodPlan, certify_hod_plan,
)
from src.backend.backtest_strategy_one_identity import (
    CertifiedIdentityPlan, certify_identity_plan,
)
from src.backend.backtest_strategy_one_pivot_store import (
    CertifiedPivotPlan, certify_pivot_plan,
)
from src.backend.backtest_strategy_one_preparation import strategy_one_v7_tickers
from src.backend.structural_v7_seed import CertifiedSeedPlan, certified_seed_plan
from src.trading_runtime.strategy_one_candidate_schema import RULE_DIGEST


@dataclass(frozen=True, slots=True)
class StrategyOneFixedPlans:
    market: CertifiedMarketDayPlan
    identities: CertifiedIdentityPlan
    execution_market: CertifiedMarketDayPlan
    prices: PriceLevelPlan
    candidates: CertifiedCandidatePlan
    activations: CertifiedActivationPlan
    pivots: CertifiedPivotPlan
    seeds: CertifiedSeedPlan
    hod: CertifiedHodPlan
    entry: CertifiedEntryEvidencePlan


def certify_strategy_one_fixed_plans(
    market: CertifiedMarketDayPlan, prices: PriceLevelPlan, *,
    market_pins: Mapping[str, Any], v7_pins: Mapping[str, Any],
    client_factory: Callable[[], Any],
) -> StrategyOneFixedPlans:
    """Cold-read every source and fail before run-context publication."""
    if (not isinstance(market, CertifiedMarketDayPlan)
            or not isinstance(prices, PriceLevelPlan)
            or market.execution_interval.kind != "fixed"
            or market.execution_interval.milliseconds != 100
            or len(market.sessions) != 1
            or prices.source_build_id != market.build_id
            or not isinstance(market_pins, Mapping)
            or not isinstance(v7_pins, Mapping)
            or not callable(client_factory)
            or market_pins.get("token") != market.token
            or market_pins.get("price_level_plan_token") != prices.token):
        raise ValueError("Strategy 1 launch lacks pinned 100ms market and price plans")
    reader = client_factory()
    if reader is None or not callable(getattr(reader, "close", None)):
        raise TypeError("Strategy 1 launch needs a closable read-only client")
    with closing(reader):
        identities = certify_identity_plan(market, client=reader)
        if identities.token != market_pins.get("strategy_one_identity_token"):
            raise ValueError("Strategy 1 dated broker identity seal changed")
        candidates = certify_candidate_plan(
            market, candidate_rule_digest=RULE_DIGEST,
            through_boundary_ms=57_600_000, client=reader)
        if (candidates.token != market_pins.get("strategy_one_candidate_token")
                or candidates.candidate_rule_digest != market_pins.get(
                    "strategy_one_candidate_rule_digest")
                or candidates.scan_query_sha256 != market_pins.get(
                    "strategy_one_scan_query_sha256")):
            raise ValueError("Strategy 1 candidate rule or full-session seal changed")
        selected = strategy_one_v7_tickers(candidates.prepared)
        if not selected:
            raise RuntimeError("Strategy 1 zero-candidate terminal authority is not typed")
        execution = project_market_day_plan(market, selected)
        projected_prices = prices.projected(execution)
        pivots = certify_pivot_plan(
            market, session_date=market.sessions[0],
            candidate_tickers=selected, client=reader)
        if pivots.token != market_pins.get("strategy_one_pivot_token"):
            raise ValueError("Strategy 1 pivot seal changed")
        activations = load_strategy_one_activations(
            market, candidates, client=reader)
        if activations.token != market_pins.get("strategy_one_activation_token"):
            raise ValueError("Strategy 1 activation seal changed")
        seeds = certified_seed_plan(execution, reader)
        if (seeds.token != v7_pins.get("token")
                or seeds.catalog_hash != v7_pins.get("catalog_hash")
                or seeds.provisional != v7_pins.get("provisional")):
            raise ValueError("Strategy 1 V7 seed seal changed")
        hod = certify_hod_plan(market, candidates, seeds, client=reader)
        if hod.token != market_pins.get("strategy_one_hod_token"):
            raise ValueError("Strategy 1 HOD seal changed")
        entry = certify_entry_evidence_plan(
            market, candidates, activations, pivots, hod, seeds,
            client=reader)
        if entry.token != market_pins.get("strategy_one_entry_token"):
            raise ValueError("Strategy 1 entry evidence seal changed")
    return StrategyOneFixedPlans(
        market, identities, execution, projected_prices, candidates, activations,
        pivots, seeds, hod, entry)
