"""Read exact Strategy 1 activation prices from certified 100ms ARTE bars.

The candidate product pins episode starts, but stores no repeated activation
price. This batched SELECT resolves each unique start once under the pinned bar
attempt. Backtest never recreates a signal, bar, indicator, or market product.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, SESSION_OPEN_OFFSET_MS, _literal,
)
from src.backend.backtest_strategy_one_candidate_store import (
    CertifiedCandidatePlan, project_candidate_plan,
)


@dataclass(frozen=True, slots=True, order=True)
class StrategyOneActivation:
    boundary_ms: int
    ticker: str
    price_int: int


@dataclass(frozen=True, slots=True)
class CertifiedActivationPlan:
    rows: tuple[StrategyOneActivation, ...]
    token: str


def project_activation_plan(
    full: CertifiedActivationPlan,
    full_candidates: CertifiedCandidatePlan, *, through_boundary_ms: int,
) -> CertifiedActivationPlan:
    """Project the full bar-certified activation schedule to a causal run.

    No bar or indicator is read here. A missing activation remains a hard
    mismatch rather than being synthesized from a later candidate price.
    """
    if not isinstance(full, CertifiedActivationPlan) or len(full.token) != 64:
        raise ValueError("Strategy 1 activation projection needs a sealed plan")
    projected = project_candidate_plan(
        full_candidates, through_boundary_ms=through_boundary_ms)
    full_starts = {(item.ticker, int(value))
                   for item in full_candidates.prepared
                   for value in item.episode_start_ms}
    actual = {(row.ticker, row.boundary_ms) for row in full.rows}
    if (len(actual) != len(full.rows) or actual != full_starts
            or any(row.price_int <= 0 for row in full.rows)):
        raise ValueError("Strategy 1 activation rows differ from full candidates")
    if projected is full_candidates:
        return full
    visible = {(item.ticker, int(value))
               for item in projected.prepared
               for value in item.episode_start_ms}
    rows = tuple(row for row in full.rows
                 if (row.ticker, row.boundary_ms) in visible)
    if len(rows) != len(visible):
        raise ValueError("Strategy 1 activation prefix is incomplete")
    token = sha256((full.token + ":candidate-prefix:" +
                    projected.token).encode()).hexdigest()
    return CertifiedActivationPlan(rows, token)


def load_strategy_one_activations(
    market: CertifiedMarketDayPlan, candidates: CertifiedCandidatePlan, *,
    client: Any, batch_size: int = 256,
) -> CertifiedActivationPlan:
    """Fail before navigation if an activation source row is absent or changed."""
    if (not isinstance(market, CertifiedMarketDayPlan)
            or not isinstance(candidates, CertifiedCandidatePlan)
            or market.execution_interval.kind != "fixed"
            or market.execution_interval.milliseconds != 100
            or len(market.sessions) != 1
            or candidates.source_build_id != market.build_id
            or type(batch_size) is not int or not 1 <= batch_size <= 256):
        raise ValueError("Strategy 1 activations need pinned 100ms authority")
    attempts = {}
    for unit in market.units:
        if unit.stage == "bars" and unit.session_date == market.sessions[0]:
            if unit.ticker in attempts:
                raise ValueError("Strategy 1 activation source has duplicate bar attempt")
            attempts[unit.ticker] = unit.attempt_id
    requested: set[tuple[str, int]] = set()
    for prepared in candidates.prepared:
        if prepared.ticker not in attempts:
            raise ValueError("Strategy 1 activation ticker lacks pinned bars")
        if len(prepared.episode_start_ms) != len(prepared.boundary_ms):
            raise ValueError("Strategy 1 activation arrays differ in length")
        for start, candidate in zip(prepared.episode_start_ms,
                                    prepared.boundary_ms):
            boundary = int(start)
            if (not 0 < boundary <= int(candidate)
                    or boundary > 57_600_000 or boundary % 100):
                raise ValueError("Strategy 1 episode start is not causal")
            requested.add((prepared.ticker, boundary))
    ordered = tuple(sorted(requested))
    found: dict[tuple[str, int], StrategyOneActivation] = {}
    for offset in range(0, len(ordered), batch_size):
        batch = ordered[offset:offset + batch_size]
        keys = ",".join(
            f"({_literal(ticker)},toUUID({_literal(attempts[ticker])}),"
            f"{(boundary + SESSION_OPEN_OFFSET_MS) // 100 - 1})"
            for ticker, boundary in batch)
        query = f"""SELECT ticker,bucket_index,resolution_ms,price_valid,close_int
          FROM arte.bars_v1 WHERE build_id={_literal(market.build_id)}
            AND session_date=toDate({_literal(market.sessions[0])})
            AND resolution_ms=100 AND (ticker,attempt_id,bucket_index)
            IN ({keys}) ORDER BY ticker,bucket_index FORMAT JSONEachRow"""
        rows = [json.loads(line) for line in client.execute(query).splitlines()
                if line.strip()]
        if len(rows) != len(batch):
            raise RuntimeError("Strategy 1 activation bar is missing or duplicate")
        for row in rows:
            ticker = str(row["ticker"])
            bucket = int(row["bucket_index"])
            boundary = (bucket + 1) * 100 - SESSION_OPEN_OFFSET_MS
            key = (ticker, boundary)
            price = row["close_int"]
            if (key not in batch or key in found
                    or int(row["resolution_ms"]) != 100
                    or int(row["price_valid"]) != 1
                    or type(price) is not int or price <= 0):
                raise RuntimeError("Strategy 1 activation bar differs from pinned signal")
            found[key] = StrategyOneActivation(boundary, ticker, price)
    if set(found) != requested:
        raise RuntimeError("Strategy 1 activation coverage is incomplete")
    result = tuple(sorted(found.values()))
    digest = sha256(b"strategy-one-activation-plan-v1\0")
    for value in (market.token, candidates.token, market.build_id,
                  market.sessions[0]):
        encoded = value.encode()
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    for item in result:
        for value in (item.ticker, str(item.boundary_ms), str(item.price_int)):
            encoded = value.encode()
            digest.update(len(encoded).to_bytes(4, "big"))
            digest.update(encoded)
    return CertifiedActivationPlan(result, digest.hexdigest())
