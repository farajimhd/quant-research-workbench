"""Compact ClickHouse persistence for completed retrospective V7 books."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable

DATABASE = "arte"
LEVELS_TABLE = f"{DATABASE}.structural_levels_v7"
COVERAGE_TABLE = f"{DATABASE}.structural_level_coverage_v7"
CHECKPOINT_TABLE = f"{DATABASE}.structural_level_builder_checkpoint_v7"
POLICY = "live_market_ssd"
PERSISTENCE_VERSION = "arte-structural-levels-v7-1"

EXPECTED_COLUMNS = {
    "structural_levels_v7": (
        "ticker", "level_id", "valid_from", "valid_to", "role", "transition_from",
        "parent_level_id", "lifecycle", "origin_session", "price", "lower", "upper",
        "association_radius", "fit_status", "fit_count", "fit_center", "fit_scale",
        "fit_lower", "fit_upper", "fit_resolution", "fit_coverage",
        "fit_degrees_of_freedom", "fit_scale_at_floor", "historical", "retrospective",
        "source_session", "source_input_hash", "source_checkpoint_hash", "state_hash",
        "publication_revision", "row_revision",
    ),
    "structural_level_coverage_v7": (
        "ticker", "session_date", "available_at", "state", "level_count",
        "interval_count", "source_input_hash", "source_checkpoint_hash",
        "parent_checkpoint_hash", "source_plan_hash", "publication_revision", "published_at",
    ),
    "structural_level_builder_checkpoint_v7": (
        "ticker", "session_date", "available_at", "checkpoint_hash",
        "parent_checkpoint_hash", "source_input_hash", "source_plan_hash",
        "checkpoint_json", "publication_revision", "row_revision", "published_at",
    ),
}


DDL = (
    f"""CREATE DATABASE IF NOT EXISTS {DATABASE}""",
    f"""CREATE TABLE IF NOT EXISTS {LEVELS_TABLE}
    (
        ticker LowCardinality(String),
        level_id String,
        valid_from DateTime64(9, 'UTC'),
        valid_to Nullable(DateTime64(9, 'UTC')),
        role LowCardinality(String),
        transition_from LowCardinality(String),
        parent_level_id String,
        lifecycle LowCardinality(String),
        origin_session Date,
        price Float64,
        lower Float64,
        upper Float64,
        association_radius Float64,
        fit_status LowCardinality(String),
        fit_count UInt32,
        fit_center Nullable(Float64),
        fit_scale Nullable(Float64),
        fit_lower Nullable(Float64),
        fit_upper Nullable(Float64),
        fit_resolution Nullable(Float64),
        fit_coverage Nullable(Float64),
        fit_degrees_of_freedom Nullable(Float64),
        fit_scale_at_floor UInt8,
        historical UInt8,
        retrospective UInt8,
        source_session Date,
        source_input_hash String,
        source_checkpoint_hash String,
        state_hash FixedString(64),
        publication_revision UInt64,
        row_revision UInt64
    )
    ENGINE = ReplacingMergeTree(row_revision)
    PARTITION BY cityHash64(ticker) % 64
    ORDER BY (ticker, level_id, valid_from)
    SETTINGS storage_policy = '{POLICY}'""",
    f"""CREATE TABLE IF NOT EXISTS {COVERAGE_TABLE}
    (
        ticker LowCardinality(String),
        session_date Date,
        available_at DateTime64(9, 'UTC'),
        state LowCardinality(String),
        level_count UInt32,
        interval_count UInt32,
        source_input_hash String,
        source_checkpoint_hash String,
        parent_checkpoint_hash String,
        source_plan_hash FixedString(64),
        publication_revision UInt64,
        published_at DateTime64(9, 'UTC')
    )
    ENGINE = ReplacingMergeTree(publication_revision)
    PARTITION BY toYYYYMM(session_date)
    ORDER BY (ticker, session_date)
    SETTINGS storage_policy = '{POLICY}'""",
    f"""CREATE TABLE IF NOT EXISTS {CHECKPOINT_TABLE}
    (
        ticker LowCardinality(String),
        session_date Date,
        available_at DateTime64(9, 'UTC'),
        checkpoint_hash String,
        parent_checkpoint_hash String,
        source_input_hash String,
        source_plan_hash FixedString(64),
        checkpoint_json String CODEC(ZSTD(3)),
        publication_revision UInt64,
        row_revision UInt64,
        published_at DateTime64(9, 'UTC')
    )
    ENGINE = ReplacingMergeTree(row_revision)
    ORDER BY ticker
    SETTINGS storage_policy = '{POLICY}'""",
)


def epoch_ns(value: int | float | str | Decimal) -> int:
    """Convert an epoch value without a binary-float multiplication."""
    result = int(Decimal(str(value)) * Decimal(1_000_000_000))
    if result < 0 or result >= 2**63:
        raise ValueError(f"Timestamp is outside DateTime64(9) range: {value!r}")
    return result


def datetime64_ns(value: int) -> str:
    seconds, nanos = divmod(value, 1_000_000_000)
    base = datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return f"{base}.{nanos:09d}"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def state_projection(level: dict[str, Any]) -> dict[str, Any]:
    fit = level.get("fit") or {}
    segments = level.get("role_segments") or []
    role = segments[-1].get("role") if segments else level.get("role", "transition")
    return {
        "level_id": str(level["id"]),
        "role": str(role or "transition"),
        "transition_from": str(level.get("transition_from") or ""),
        "parent_level_id": str(level.get("parent_id") or ""),
        "lifecycle": "qualified" if level.get("qualified", True) else "candidate",
        "origin_session": str(level["origin_session"]),
        "price": float(level["price"]),
        "lower": float(level["lower"]),
        "upper": float(level["upper"]),
        "association_radius": float(level.get("association_radius") or 0.0),
        "fit_status": str(fit.get("status") or "unknown"),
        "fit_count": int(fit.get("count") or len(level.get("observations") or ())),
        "fit_center": fit.get("center"),
        "fit_scale": fit.get("scale"),
        "fit_lower": fit.get("lower"),
        "fit_upper": fit.get("upper"),
        "fit_resolution": fit.get("resolution"),
        "fit_coverage": fit.get("coverage"),
        "fit_degrees_of_freedom": fit.get("degrees_of_freedom"),
        "fit_scale_at_floor": int(bool(fit.get("scale_at_floor"))),
        "historical": int(bool(level.get("historical"))),
    }


def projection_hash(value: dict[str, Any]) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


@dataclass
class OpenInterval:
    projection: dict[str, Any]
    state_hash: str
    valid_from_ns: int
    source_session: str
    source_input_hash: str
    source_checkpoint_hash: str
    publication_revision: int


def interval_row(ticker: str, opened: OpenInterval, valid_to_ns: int | None) -> dict[str, Any]:
    p = opened.projection
    return {
        "ticker": ticker,
        **p,
        "valid_from": datetime64_ns(opened.valid_from_ns),
        "valid_to": None if valid_to_ns is None else datetime64_ns(valid_to_ns),
        "retrospective": 1,
        "source_session": opened.source_session,
        "source_input_hash": opened.source_input_hash,
        "source_checkpoint_hash": opened.source_checkpoint_hash,
        "state_hash": opened.state_hash,
        "publication_revision": opened.publication_revision,
        "row_revision": max(opened.publication_revision, valid_to_ns or 0),
    }


def compact_checkpoints(checkpoints: Iterable[dict[str, Any]], source_plan_hash: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Convert ordered full-session books to compact retrospective intervals."""
    opened: dict[str, OpenInterval] = {}
    intervals: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    previous_ns = -1
    terminal: dict[str, Any] | None = None
    ticker: str | None = None
    for book in checkpoints:
        if not book.get("retrospective"):
            raise ValueError("V7 historical checkpoint is not retrospective")
        if ticker is None:
            ticker = str(book["ticker"])
        if str(book["ticker"]) != ticker:
            raise ValueError("A compacted stream may contain only one ticker")
        stamp = epoch_ns(book["available_at"])
        if stamp <= previous_ns:
            raise ValueError("V7 checkpoints must be strictly ordered by availability")
        previous_ns = stamp
        current: dict[str, dict[str, Any]] = {}
        for level in book.get("levels") or ():
            if not level.get("qualified", True):
                continue
            projection = state_projection(level)
            level_id = projection["level_id"]
            if level_id in current:
                raise ValueError(f"Duplicate V7 level identity {level_id}")
            current[level_id] = projection
        for level_id in sorted(set(opened) - set(current)):
            intervals.append(interval_row(ticker, opened.pop(level_id), stamp))
        for level_id, projection in sorted(current.items()):
            prior = opened.get(level_id)
            # Comparing the already-normalized projection avoids serializing and
            # hashing every unchanged level in every daily full-book checkpoint.
            # The hash is needed only when a new interval is actually opened.
            if prior is not None and prior.projection == projection:
                continue
            if prior is not None:
                intervals.append(interval_row(ticker, prior, stamp))
            state_hash = projection_hash(projection)
            opened[level_id] = OpenInterval(
                projection=projection,
                state_hash=state_hash,
                valid_from_ns=stamp,
                source_session=str(book["session"]),
                source_input_hash=str(book["input_hash"]),
                source_checkpoint_hash=str(book["checkpoint_hash"]),
                publication_revision=stamp,
            )
        coverage.append({
            "ticker": ticker,
            "session_date": str(book["session"]),
            "available_at": datetime64_ns(stamp),
            "state": "complete",
            "level_count": len(current),
            "interval_count": len(intervals) + len(opened),
            "source_input_hash": str(book["input_hash"]),
            "source_checkpoint_hash": str(book["checkpoint_hash"]),
            "parent_checkpoint_hash": str(book.get("prior_checkpoint_hash") or ""),
            "source_plan_hash": source_plan_hash,
            "publication_revision": stamp,
        })
        terminal = book
    if terminal is None or ticker is None:
        raise ValueError("No V7 checkpoints supplied")
    intervals.extend(interval_row(ticker, item, None) for _, item in sorted(opened.items()))
    return intervals, coverage, terminal


def source_plan_digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def encode_rows(rows: Iterable[dict[str, Any]]) -> str:
    return "\n".join(canonical_json(row) for row in rows)
