"""SELECT-only launch certificate for one numbered Strategy 1 session.

All full-session producer seals are checked before opening a journal run gate.
The returned execution projection is only an in-memory read scope; it never
replaces the certified parent population or writes ARTE market products.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
import re
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
from src.backend.backtest_strategy_one_v7_interval_store import (
    CertifiedV7IntervalPlan, certify_v7_interval_plan,
)
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
    v7_intervals: CertifiedV7IntervalPlan
    hod: CertifiedHodPlan
    entry: CertifiedEntryEvidencePlan


def complete_strategy_one_seed_payload(
    payload: Mapping[str, Any], market: CertifiedMarketDayPlan,
    execution: CertifiedMarketDayPlan,
) -> bool:
    """Projection markers alone cannot stand in for a typed V7 seed proof."""
    digest = re.compile(r"[0-9a-f]{64}\Z")
    return bool(
        isinstance(payload, Mapping)
        and payload.get("schema_version") == "typed-v7-seed-plan-v1"
        and payload.get("build_id") == market.build_id == execution.build_id
        and type(payload.get("unit_count")) is int
        and payload["unit_count"] == len(execution.tickers)
        and type(payload.get("provisional")) is bool
        and digest.fullmatch(str(payload.get("token") or ""))
        and digest.fullmatch(str(payload.get("catalog_hash") or ""))
        and payload.get("market_projection_token") == execution.token
        and payload.get("parent_market_plan_token") == market.token
    )


def certify_independent_strategy_one_products(
    market: CertifiedMarketDayPlan, candidates: CertifiedCandidatePlan,
    selected: tuple[str, ...], execution: CertifiedMarketDayPlan, *,
    client_factory: Callable[[], Any],
    seed_client_factory: Callable[[], Any] | None = None,
    pool: ThreadPoolExecutor | None = None,
) -> tuple[CertifiedPivotPlan, CertifiedActivationPlan, CertifiedSeedPlan]:
    """Certify independent V7 inputs concurrently, each on its own read socket."""
    if (not isinstance(market, CertifiedMarketDayPlan)
            or not isinstance(candidates, CertifiedCandidatePlan)
            or not isinstance(execution, CertifiedMarketDayPlan)
            or len(market.sessions) != 1
            or candidates.source_build_id != market.build_id
            or execution.build_id != market.build_id
            or execution.sessions != market.sessions
            or tuple(execution.tickers) != tuple(selected)
            or not selected or len(set(selected)) != len(selected)
            or not callable(client_factory)
            or (seed_client_factory is not None
                and not callable(seed_client_factory))):
        raise ValueError("Strategy 1 independent certificate scope is invalid")

    def read(factory, operation):
        reader = factory()
        if reader is None or not callable(getattr(reader, "close", None)):
            raise TypeError("Strategy 1 certificate needs a closable read client")
        with closing(reader):
            return operation(reader)

    def collect(workers):
        pivot_future = workers.submit(read, client_factory, lambda reader:
            certify_pivot_plan(market, session_date=market.sessions[0],
                               candidate_tickers=selected, client=reader))
        activation_future = workers.submit(read, client_factory, lambda reader:
            load_strategy_one_activations(market, candidates, client=reader))
        seed_future = workers.submit(read, seed_client_factory or client_factory,
                                     lambda reader: certified_seed_plan(execution, reader))
        return (pivot_future.result(), activation_future.result(),
                seed_future.result())

    if pool is not None:
        return collect(pool)
    with ThreadPoolExecutor(max_workers=3,
                            thread_name_prefix="strategy-one-seals") as workers:
        return collect(workers)


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
    def read(operation, *args, **kwargs):
        reader = client_factory()
        if reader is None or not callable(getattr(reader, "close", None)):
            raise TypeError("Strategy 1 launch needs a closable read-only client")
        with closing(reader):
            return operation(*args, reader, **kwargs)

    # Separate clients prevent concurrent use of one HTTP socket. The pool is
    # bounded, and dependent seals are checked only after their inputs pass.
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="strategy-one-seals") as pool:
        identity_future = pool.submit(
            read, lambda plan, reader: certify_identity_plan(plan, client=reader), market)
        candidate_future = pool.submit(
            read, lambda plan, reader: certify_candidate_plan(
                plan, candidate_rule_digest=RULE_DIGEST,
                through_boundary_ms=57_600_000, client=reader), market)
        identities = identity_future.result()
        if identities.token != market_pins.get("strategy_one_identity_token"):
            raise ValueError("Strategy 1 dated broker identity seal changed")
        candidates = candidate_future.result()
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
        pivots, activations, seeds = certify_independent_strategy_one_products(
            market, candidates, selected, execution,
            client_factory=client_factory, pool=pool)
        if pivots.token != market_pins.get("strategy_one_pivot_token"):
            raise ValueError("Strategy 1 pivot seal changed")
        if activations.token != market_pins.get("strategy_one_activation_token"):
            raise ValueError("Strategy 1 activation seal changed")
        if (seeds.token != v7_pins.get("token")
                or seeds.catalog_hash != v7_pins.get("catalog_hash")
                or seeds.provisional != v7_pins.get("provisional")):
            raise ValueError("Strategy 1 V7 seed seal changed")
    with ThreadPoolExecutor(max_workers=1,
                            thread_name_prefix="strategy-one-v7-intervals") as pool:
        interval_future = pool.submit(
            read, lambda plan, reader: certify_v7_interval_plan(
                plan, seeds, session_date=market.sessions[0],
                candidate_tickers=selected, client=reader), execution)
        hod = read(lambda plan, reader: certify_hod_plan(
            plan, candidates, seeds, client=reader), market)
        if hod.token != market_pins.get("strategy_one_hod_token"):
            raise ValueError("Strategy 1 HOD seal changed")
        entry = read(lambda plan, reader: certify_entry_evidence_plan(
            plan, candidates, activations, pivots, hod, seeds,
            client=reader), market)
        if entry.token != market_pins.get("strategy_one_entry_token"):
            raise ValueError("Strategy 1 entry evidence seal changed")
        intervals = interval_future.result()
    if intervals.token != market_pins.get("strategy_one_v7_interval_token"):
        raise ValueError("Strategy 1 V7 interval seal changed")
    return StrategyOneFixedPlans(
        market, identities, execution, projected_prices, candidates, activations,
        pivots, seeds, intervals, hod, entry)
