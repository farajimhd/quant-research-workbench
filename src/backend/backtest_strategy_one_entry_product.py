"""Canonical scalar projection for producer-owned Strategy 1 entry evidence.

This module writes nothing. The producer may publish these rows after deriving
them from certified ARTE inputs; Backtest may only read a coverage-sealed
attempt. The full V7 book, bars, and indicator values are never copied here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from math import isfinite
from struct import pack, unpack
from typing import Mapping, Sequence

from src.backend.backtest_strategy_one_evidence import StrategyOneEntryEvidence
from src.trading_runtime.strategy_one_activation_state import FrozenActivation


_PRICE_QUANTUM = Decimal("0.0001")


def canonical_price(value: float) -> float:
    """Use the same 1/10,000 price unit as certified ARTE bars.

    Floating subtraction of a tick may leave a sub-unit binary tail. A value
    genuinely outside the persisted price grid fails instead of silently
    changing the strategy's financial decision.
    """
    if type(value) is not float or not isfinite(value) or value <= 0:
        raise ValueError("Strategy 1 price is not finite and positive")
    source = Decimal(str(value))
    rounded = source.quantize(_PRICE_QUANTUM)
    if abs(source - rounded) > Decimal("0.000000005"):
        raise ValueError("Strategy 1 price is outside the ARTE 1/10000 grid")
    return float(rounded)


def exact_float64(bits: object) -> float:
    """Restore exact stored Float64 bits; JSONEachRow rounds decimal display."""
    value = int(bits)
    if not 0 <= value < 2**64:
        raise ValueError("Strategy 1 Float64 bits are invalid")
    result = unpack("<d", pack("<Q", value))[0]
    if not isfinite(result):
        raise ValueError("Strategy 1 Float64 value is not finite")
    return result


@dataclass(frozen=True, slots=True)
class ActivationFact:
    ticker: str
    episode_start_ms: int
    price_int: int
    average_gap: float | None
    resistance_ids: tuple[str, ...]

    def row(self) -> dict:
        return {"episode_start_ms": self.episode_start_ms,
                "price_int": self.price_int, "average_gap": self.average_gap,
                "resistance_count": len(self.resistance_ids)}

    def resistance_rows(self) -> tuple[dict, ...]:
        return tuple({"episode_start_ms": self.episode_start_ms,
                      "ordinal": index, "level_id": level_id}
                     for index, level_id in enumerate(self.resistance_ids, 1))


@dataclass(frozen=True, slots=True)
class CandidateFact:
    ticker: str
    boundary_ms: int
    episode_start_ms: int
    bos_break_boundary_ms: int | None
    bos_pivot_id: str
    bos_break_close_int: int | None
    bos_support_kind: str
    bos_support_level_id: str
    bos_support_pivot_id: str
    protection_valid: bool
    stop_price: float | None
    target_price: float | None
    target_level_id: str
    target_ordinal: int | None

    def row(self) -> dict:
        return {"boundary_ms": self.boundary_ms,
                "episode_start_ms": self.episode_start_ms,
                "bos_break_boundary_ms": self.bos_break_boundary_ms,
                "bos_pivot_id": self.bos_pivot_id,
                "bos_break_close_int": self.bos_break_close_int,
                "bos_support_kind": self.bos_support_kind,
                "bos_support_level_id": self.bos_support_level_id,
                "bos_support_pivot_id": self.bos_support_pivot_id,
                "protection_valid": int(self.protection_valid),
                "stop_price": self.stop_price,
                "target_price": self.target_price,
                "target_level_id": self.target_level_id,
                "target_ordinal": self.target_ordinal}


def project_activation(value: FrozenActivation) -> ActivationFact:
    if (not isinstance(value, FrozenActivation)
            or not value.ticker or value.ticker != value.ticker.upper()
            or type(value.boundary_ms) is not int
            or not 0 < value.boundary_ms <= 57_600_000
            or value.boundary_ms % 100
            or type(value.price_int) is not int or value.price_int <= 0
            or value.average_gap is not None
            and (type(value.average_gap) is not float
                 or not isfinite(value.average_gap) or value.average_gap <= 0)
            or not isinstance(value.resistance_ids, tuple)
            or len(value.resistance_ids) > 1024
            or any(not isinstance(identity, str) or not identity
                   or len(identity) > 256 for identity in value.resistance_ids)
            or len(value.resistance_ids) != len(set(value.resistance_ids))):
        raise ValueError("Strategy 1 frozen activation cannot be normalized")
    return ActivationFact(value.ticker, value.boundary_ms, value.price_int,
                          value.average_gap, value.resistance_ids)


def project_candidate(value: StrategyOneEntryEvidence) -> CandidateFact:
    if not isinstance(value, StrategyOneEntryEvidence):
        raise TypeError("Strategy 1 entry evidence must be typed")
    cursor = value.candidate.evidence
    market = value.candidate.market_row
    boundary, ticker = cursor.boundary_ms, cursor.ticker
    if (type(boundary) is not int or not 0 < boundary <= 57_600_000
            or boundary % 100 or not ticker or ticker != ticker.upper()
            or market.get("boundary_ms") != boundary
            or market.get("ticker") != ticker
            or value.activation.ticker != ticker
            or value.activation.boundary_ms != cursor.episode_start_ms
            or value.bos.ticker != ticker
            or value.bos.as_of_boundary_ms != boundary):
        raise ValueError("Strategy 1 entry evidence identities differ")
    broken = value.bos.open_break
    if broken is None:
        break_at = close_int = None
        pivot_id = ""
    else:
        break_at = broken.boundary_ms
        close_int = broken.close_int
        pivot_id = broken.broken_pivot.pivot_id
        if (type(break_at) is not int or not 0 < break_at <= boundary
                or break_at % 1_000 or type(close_int) is not int
                or close_int <= 0 or not pivot_id
                or broken.broken_pivot.confirmed_boundary_ms > break_at):
            raise ValueError("Strategy 1 BOS break is not completed")
    support = value.bos_support
    if support is None:
        support_kind = support_level = support_pivot = ""
    else:
        support_kind, support_level = support.kind, support.level_id
        support_pivot = support.pivot_id or ""
        if (broken is None or not support_kind or not support_level
                or any(len(item) > 256 for item in
                       (support_kind, support_level, support_pivot))):
            raise ValueError("Strategy 1 BOS support lacks a completed break")
    protection = value.protection
    if protection is None:
        stop = target = None
        target_id = ""
        ordinal = None
    else:
        stop = protection.state.stop
        target = protection.state.target
        stop_source = protection.stop_amendment
        target_source = protection.target_amendment
        if (protection.state.boundary_ms != boundary
                or type(stop) is not float or type(target) is not float
                or not isfinite(stop) or not isfinite(target)
                or not 0 < stop < target
                or not isinstance(stop_source, Mapping)
                or stop_source.get("source") != "completed_30s_bar_low"
                or stop_source.get("boundary_ms") !=
                   cursor.stop_bar_boundary_ms
                or stop_source.get("low_int") != cursor.stop_low_int
                or stop_source.get("price") != stop
                or not isinstance(target_source, Mapping)
                or target_source.get("price") != target
                or target_source.get("ordinal") != 3
                or not isinstance(target_source.get("level"), Mapping)
                or not target_source["level"].get("unified_level_id")):
            raise ValueError("Strategy 1 initial protection differs from completed sources")
        target_id = str(target_source["level"]["unified_level_id"])
        ordinal = 3
        stop = canonical_price(stop)
        target = canonical_price(target)
    return CandidateFact(
        ticker, boundary, cursor.episode_start_ms, break_at, pivot_id,
        close_int, support_kind, support_level, support_pivot,
        protection is not None, stop, target, target_id, ordinal)


def content_hash(activations: Sequence[ActivationFact],
                 candidates: Sequence[CandidateFact]) -> str:
    """One deterministic ticker-day seal over every normalized scalar row."""
    digest = sha256(b"strategy-one-entry-evidence-content-v1\0")
    episodes: set[int] = set()
    ticker: str | None = None
    prior = 0

    def add(value: object) -> None:
        if value is None:
            encoded = b"N"
        elif type(value) is bool:
            encoded = b"B1" if value else b"B0"
        elif type(value) is int:
            encoded = b"I" + str(value).encode()
        elif type(value) is float and isfinite(value):
            encoded = b"F" + value.hex().encode()
        elif type(value) is str:
            encoded = b"S" + value.encode("utf-8")
        else:
            raise ValueError("Strategy 1 product contains a non-scalar value")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)

    add(len(activations))
    add(len(candidates))
    for activation in activations:
        if (not isinstance(activation, ActivationFact)
                or ticker is not None and activation.ticker != ticker
                or not activation.ticker
                or activation.ticker != activation.ticker.upper()
                or not prior < activation.episode_start_ms <= 57_600_000
                or activation.episode_start_ms % 100
                or activation.price_int <= 0
                or activation.average_gap is not None
                and (type(activation.average_gap) is not float
                     or not isfinite(activation.average_gap)
                     or activation.average_gap <= 0)
                or not isinstance(activation.resistance_ids, tuple)
                or len(activation.resistance_ids) > 1024
                or any(not isinstance(identity, str) or not identity
                       or len(identity) > 256
                       for identity in activation.resistance_ids)
                or len(activation.resistance_ids) != len(set(activation.resistance_ids))):
            raise ValueError("Strategy 1 activation product is unordered")
        ticker = activation.ticker
        prior = activation.episode_start_ms
        episodes.add(prior)
        for value in (activation.ticker, activation.episode_start_ms, activation.price_int,
                      activation.average_gap, len(activation.resistance_ids),
                      *activation.resistance_ids):
            add(value)
    prior = 0
    for candidate in candidates:
        if (not isinstance(candidate, CandidateFact)
                or candidate.ticker != ticker
                or not prior < candidate.boundary_ms <= 57_600_000
                or candidate.boundary_ms % 100
                or candidate.episode_start_ms not in episodes
                or candidate.episode_start_ms > candidate.boundary_ms
                or type(candidate.protection_valid) is not bool
                or candidate.protection_valid != (candidate.stop_price is not None)
                or candidate.protection_valid != (candidate.target_price is not None)
                or candidate.protection_valid and (
                    type(candidate.stop_price) is not float
                    or type(candidate.target_price) is not float
                    or not isfinite(candidate.stop_price)
                    or not isfinite(candidate.target_price)
                    or not 0 < candidate.stop_price < candidate.target_price
                    or canonical_price(candidate.stop_price) != candidate.stop_price
                    or canonical_price(candidate.target_price) != candidate.target_price
                    or not candidate.target_level_id
                    or candidate.target_ordinal != 3)
                or not candidate.protection_valid and (
                    candidate.target_level_id or candidate.target_ordinal is not None)
                or candidate.bos_break_boundary_ms is None and (
                    candidate.bos_pivot_id or candidate.bos_break_close_int is not None
                    or candidate.bos_support_kind or candidate.bos_support_level_id
                    or candidate.bos_support_pivot_id)
                or candidate.bos_break_boundary_ms is not None and (
                    not 0 < candidate.bos_break_boundary_ms <= candidate.boundary_ms
                    or candidate.bos_break_boundary_ms % 1_000
                    or not candidate.bos_pivot_id
                    or candidate.bos_break_close_int is None
                    or candidate.bos_break_close_int <= 0)
                or bool(candidate.bos_support_kind) != bool(
                    candidate.bos_support_level_id)):
            raise ValueError("Strategy 1 candidate product is incomplete or unordered")
        prior = candidate.boundary_ms
        add(candidate.ticker)
        for value in candidate.row().values():
            add(value)
    if not candidates or not activations:
        raise ValueError("Strategy 1 entry product requires candidate coverage")
    return digest.hexdigest()
