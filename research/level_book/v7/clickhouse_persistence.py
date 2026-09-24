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
OBSERVATIONS_TABLE = f"{DATABASE}.structural_level_observations_v7"
POLICY = "live_market_ssd"
PERSISTENCE_VERSION = "arte-structural-levels-v7-2"

EXPECTED_COLUMNS = {
    "structural_levels_v7": (
        "ticker", "level_id", "valid_from", "valid_to", "role", "transition_from",
        "parent_level_id", "lifecycle", "origin_session", "price", "lower", "upper",
        "association_radius", "fit_status", "fit_distribution", "fit_count", "fit_center", "fit_scale",
        "fit_lower", "fit_upper", "fit_resolution", "fit_coverage",
        "fit_degrees_of_freedom", "fit_scale_at_floor", "historical", "retrospective",
        "source_session", "source_input_hash", "source_checkpoint_hash", "state_hash",
        "publication_revision", "row_revision",
    ),
    "structural_level_coverage_v7": (
        "ticker", "session_date", "available_at", "state", "level_count",
        "interval_count", "observation_count", "observation_interval_count",
        "input_policy", "source_extraction_version", "band_config_hash",
        "source_input_hash", "source_checkpoint_hash",
        "parent_checkpoint_hash", "source_plan_hash", "publication_revision", "published_at",
    ),
    "structural_level_observations_v7": (
        "ticker", "observation_id", "level_id", "valid_from", "valid_to",
        "price", "resolution", "at", "resolved_at", "role", "session_date",
        "source_session", "source_checkpoint_hash", "publication_revision", "row_revision",
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
        fit_distribution LowCardinality(String),
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
        observation_count UInt32,
        observation_interval_count UInt64,
        input_policy LowCardinality(String),
        source_extraction_version LowCardinality(String),
        band_config_hash FixedString(64),
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
    f"""CREATE TABLE IF NOT EXISTS {OBSERVATIONS_TABLE}
    (
        ticker LowCardinality(String),
        observation_id FixedString(64),
        level_id String,
        valid_from DateTime64(9, 'UTC'),
        valid_to Nullable(DateTime64(9, 'UTC')),
        price Float64,
        resolution Float64,
        at DateTime64(9, 'UTC'),
        resolved_at DateTime64(9, 'UTC'),
        role LowCardinality(String),
        session_date Date,
        source_session Date,
        source_checkpoint_hash String,
        publication_revision UInt64,
        row_revision UInt64
    )
    ENGINE = ReplacingMergeTree(row_revision)
    PARTITION BY cityHash64(ticker) % 64
    ORDER BY (ticker, observation_id, valid_from)
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
        "fit_distribution": str(fit.get("distribution") or ""),
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


def observation_projection(observation: dict[str, Any]) -> dict[str, Any]:
    return {key: observation[key] for key in ("price", "resolution", "at", "resolved_at", "role", "session")}


def observation_interval_row(ticker: str, item: dict[str, Any], valid_to_ns: int | None) -> dict[str, Any]:
    observation = item["observation"]
    return {
        "ticker": ticker, "observation_id": item["observation_id"], "level_id": item["level_id"],
        "valid_from": datetime64_ns(item["valid_from_ns"]),
        "valid_to": None if valid_to_ns is None else datetime64_ns(valid_to_ns),
        "price": float(observation["price"]), "resolution": float(observation["resolution"]),
        "at": datetime64_ns(epoch_ns(observation["at"])),
        "resolved_at": datetime64_ns(epoch_ns(observation["resolved_at"])),
        "role": str(observation["role"]), "session_date": str(observation["session"]),
        "source_session": item["source_session"],
        "source_checkpoint_hash": item["source_checkpoint_hash"],
        "publication_revision": item["valid_from_ns"],
        "row_revision": max(item["valid_from_ns"], valid_to_ns or 0),
    }


def compact_checkpoints(checkpoints: Iterable[dict[str, Any]], source_plan_hash: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert ordered books to qualified/candidate and observation intervals."""
    opened: dict[str, OpenInterval] = {}
    opened_observations: dict[str, dict[str, Any]] = {}
    observation_ids: dict[tuple[Any, ...], str] = {}
    intervals: list[dict[str, Any]] = []
    observation_intervals: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    previous_ns = -1
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
        current_observations: dict[str, tuple[str, dict[str, Any]]] = {}
        occurrences: dict[tuple[Any, ...], int] = {}
        for level in book.get("levels") or ():
            projection = state_projection(level)
            level_id = projection["level_id"]
            if level_id in current:
                raise ValueError(f"Duplicate V7 level identity {level_id}")
            current[level_id] = projection
            for observation in level.get("observations") or ():
                value = observation_projection(observation)
                content = tuple(value[key] for key in ("price", "resolution", "at", "resolved_at", "role", "session"))
                occurrence = occurrences.get(content, 0)
                occurrences[content] = occurrence + 1
                identity = (*content, occurrence)
                observation_id = observation_ids.get(identity)
                if observation_id is None:
                    observation_id = sha256(canonical_json(identity).encode()).hexdigest()
                    observation_ids[identity] = observation_id
                current_observations[observation_id] = (level_id, value)
        for observation_id in sorted(set(opened_observations) - set(current_observations)):
            observation_intervals.append(observation_interval_row(ticker, opened_observations.pop(observation_id), stamp))
        for observation_id, (level_id, observation) in sorted(current_observations.items()):
            prior_observation = opened_observations.get(observation_id)
            if prior_observation is not None and prior_observation["level_id"] == level_id:
                continue
            if prior_observation is not None:
                observation_intervals.append(observation_interval_row(ticker, prior_observation, stamp))
            opened_observations[observation_id] = {
                "observation_id": observation_id, "level_id": level_id,
                "observation": observation, "valid_from_ns": stamp,
                "source_session": str(book["session"]),
                "source_checkpoint_hash": str(book["checkpoint_hash"]),
            }
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
            "observation_count": len(current_observations),
            "observation_interval_count": len(observation_intervals) + len(opened_observations),
            "input_policy": str(book.get("input_policy") or "legacy-unfiltered"),
            "source_extraction_version": str(book.get("source_extraction_version") or ""),
            "band_config_hash": projection_hash(book.get("band_config") or {}),
            "source_input_hash": str(book["input_hash"]),
            "source_checkpoint_hash": str(book["checkpoint_hash"]),
            "parent_checkpoint_hash": str(book.get("prior_checkpoint_hash") or ""),
            "source_plan_hash": source_plan_hash,
            "publication_revision": stamp,
        })
    if ticker is None:
        raise ValueError("No V7 checkpoints supplied")
    intervals.extend(interval_row(ticker, item, None) for _, item in sorted(opened.items()))
    observation_intervals.extend(observation_interval_row(ticker, item, None) for _, item in sorted(opened_observations.items()))
    return intervals, observation_intervals, coverage


def source_plan_digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def encode_rows(rows: Iterable[dict[str, Any]]) -> str:
    return "\n".join(canonical_json(row) for row in rows)
