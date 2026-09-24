"""Read-only reconstruction of a prior-session V7 streaming seed from arte.

The retrospective intervals are available only at their session-end publication
time.  Current-session observations are never read from these tables by Backtest.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any
from zoneinfo import ZoneInfo

from src.backend.backtest_market_data import assert_select_only
from src.market_engine.derived_trade_policy import POLICY
from src.market_engine.historical_level_checkpoint import digest
from src.market_engine.reaction_band import CONFIG as BAND_CONFIG
from src.market_engine.streaming_level_book import EXTRACTION_VERSION


_NY = ZoneInfo("America/New_York")
_LEVELS = "arte.structural_levels_v7"
_OBSERVATIONS = "arte.structural_level_observations_v7"
_COVERAGE = "arte.structural_level_coverage_v7"
_BAND_CONFIG = {**BAND_CONFIG, "coverage": .8}
_PROVISIONAL_INPUT_POLICY = "legacy-unfiltered"


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _rows(client: Any, query: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(assert_select_only(query)).splitlines()
            if line.strip()]


def _epoch(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace(" ", "T"))
    return parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()


def _cutoff(session: date) -> str:
    utc = datetime.combine(session, time(4), tzinfo=_NY).astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%d %H:%M:%S.%f") + "000"


def _band_hash() -> str:
    return sha256(json.dumps(_BAND_CONFIG, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode()).hexdigest()


def _validate_coverage(row: dict[str, Any], *, ticker: str, session: date) -> None:
    empty_seed = (int(row["level_count"]) == 0 and int(row["observation_count"]) == 0)
    initial_empty = (empty_seed and row["state"] == "empty" and
                     not row["input_policy"] and not row["source_extraction_version"] and
                     row["band_config_hash"] == "0" * 64)
    if (str(row["ticker"]) != ticker
            or str(row["session_date"]) >= session.isoformat()
            or row["state"] not in {"complete", "empty"}
            or (not empty_seed and row["input_policy"] not in {POLICY, _PROVISIONAL_INPUT_POLICY})
            or (not initial_empty and row["source_extraction_version"] != EXTRACTION_VERSION)
            or (not initial_empty and row["band_config_hash"] != _band_hash())
            or int(row["level_count"]) < 0
            or int(row["observation_count"]) < 0):
        raise ValueError(f"Prior V7 state is not compatible with provisional V1 Backtest: {session} {ticker}")


@dataclass(frozen=True, slots=True)
class CertifiedSeedPlan:
    build_id: str
    catalog_hash: str
    units: tuple[dict[str, Any], ...]
    token: str
    provisional: bool

    def payload(self) -> dict[str, Any]:
        return {"schema_version": "typed-v7-seed-plan-v1", "build_id": self.build_id,
                "catalog_hash": self.catalog_hash, "unit_count": len(self.units),
                "token": self.token, "provisional": self.provisional,
                "source_tables": [_LEVELS, _OBSERVATIONS, _COVERAGE]}


def certified_seed_plan(market: Any, client: Any) -> CertifiedSeedPlan:
    """Batch-check every selected ticker's filtered prior seed before Backtest."""
    bars = {(unit.session_date, unit.ticker) for unit in market.units if unit.stage == "bars"}
    if not bars:
        raise ValueError("V7 seed preflight requires pinned market-day bars")
    units: list[dict[str, Any]] = []
    plan_hashes: set[str] = set()
    policies: set[str] = set()
    for day in market.sessions:
        session = date.fromisoformat(day)
        tickers = sorted(ticker for unit_day, ticker in bars if unit_day == day)
        for offset in range(0, len(tickers), 256):
            batch = tickers[offset:offset + 256]
            names = ",".join(_literal(ticker) for ticker in batch)
            rows = _rows(client,
                f"SELECT * FROM {_COVERAGE} FINAL WHERE ticker IN ({names}) "
                f"AND available_at<=toDateTime64({_literal(_cutoff(session))},9,'UTC') "
                "ORDER BY ticker,available_at DESC LIMIT 1 BY ticker FORMAT JSONEachRow")
            found = {str(row["ticker"]): row for row in rows}
            if len(rows) != len(batch) or set(found) != set(batch):
                raise ValueError(f"V7 prior coverage is missing or duplicated for {day}")
            for ticker in batch:
                row = found[ticker]
                _validate_coverage(row, ticker=ticker, session=session)
                plan_hashes.add(str(row["source_plan_hash"]))
                if int(row["level_count"]) > 0:
                    policies.add(str(row["input_policy"]))
                units.append({key: row[key] for key in (
                    "ticker", "session_date", "available_at", "source_checkpoint_hash",
                    "source_plan_hash", "level_count", "observation_count", "input_policy")}
                    | {"backtest_session": day})
    if len(plan_hashes) != 1 or len(next(iter(plan_hashes))) != 64:
        raise ValueError("V7 prior seeds mix source campaigns")
    if len(policies) > 1:
        raise ValueError("V7 prior seeds mix provisional and filtered inputs")
    token = sha256(json.dumps(units, sort_keys=True, separators=(",", ":"),
                              allow_nan=False).encode()).hexdigest()
    return CertifiedSeedPlan(market.build_id, next(iter(plan_hashes)), tuple(units), token,
                             _PROVISIONAL_INPUT_POLICY in policies)


def preceding_coverage(client: Any, *, ticker: str, session: date) -> dict[str, Any]:
    """Pin the latest completed retrospective state known by 04:00 ET."""
    rows = _rows(client,
        f"SELECT * FROM {_COVERAGE} FINAL WHERE ticker={_literal(ticker)} "
        f"AND available_at<=toDateTime64({_literal(_cutoff(session))},9,'UTC') "
        "ORDER BY available_at DESC LIMIT 1 FORMAT JSONEachRow")
    if len(rows) != 1:
        raise ValueError(f"Missing prior V7 coverage for {session} {ticker}")
    row = rows[0]
    _validate_coverage(row, ticker=ticker, session=session)
    return row


def load_seed(client: Any, *, ticker: str, session: date,
              coverage: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load one complete typed seed; any missing level/observation fails closed."""
    pinned = coverage or preceding_coverage(client, ticker=ticker, session=session)
    current = preceding_coverage(client, ticker=ticker, session=session)
    if any(current.get(key) != pinned.get(key) for key in
           ("session_date", "available_at", "source_checkpoint_hash", "source_plan_hash")):
        raise ValueError("Prior V7 coverage changed after preflight")
    cutoff = _literal(_cutoff(session))
    predicate = (f"ticker={_literal(ticker)} AND valid_from<=toDateTime64({cutoff},9,'UTC') "
                 f"AND (isNull(valid_to) OR valid_to>toDateTime64({cutoff},9,'UTC'))")
    levels = _rows(client, f"SELECT * FROM {_LEVELS} FINAL WHERE {predicate} "
                   "ORDER BY level_id FORMAT JSONEachRow")
    observations = _rows(client, f"SELECT * FROM {_OBSERVATIONS} FINAL WHERE {predicate} "
                         "ORDER BY level_id,observation_id FORMAT JSONEachRow")
    if len(levels) != int(pinned["level_count"]) or len(observations) != int(pinned["observation_count"]):
        raise ValueError(f"Prior V7 rows differ from coverage: {session} {ticker}")
    by_id: dict[str, dict[str, Any]] = {}
    for row in levels:
        level_id = str(row["level_id"])
        if level_id in by_id:
            raise ValueError("Duplicate prior V7 level interval")
        fit = {"status": row["fit_status"], "count": int(row["fit_count"])}
        for field in ("distribution", "center", "scale", "lower", "upper", "resolution",
                      "coverage", "degrees_of_freedom", "scale_at_floor"):
            value = row["fit_" + field]
            if value is not None and value != "":
                fit[field] = bool(value) if field == "scale_at_floor" else value
        level = {
            "id": level_id, "origin_session": str(row["origin_session"]),
            "price": float(row["price"]), "lower": float(row["lower"]),
            "upper": float(row["upper"]), "association_radius": float(row["association_radius"]),
            "qualified": row["lifecycle"] == "qualified", "historical": bool(row["historical"]),
            "role": str(row["role"]), "role_segments": [{"role": str(row["role"])}],
            "fit": fit, "observations": [],
        }
        if row.get("parent_level_id"):
            level["parent_id"] = str(row["parent_level_id"])
        if row.get("transition_from"):
            level["transition_from"] = str(row["transition_from"])
        by_id[level_id] = level
    identities: set[str] = set()
    for row in observations:
        identity = str(row["observation_id"])
        if identity in identities or str(row["level_id"]) not in by_id:
            raise ValueError("Duplicate or orphaned prior V7 observation")
        identities.add(identity)
        by_id[str(row["level_id"])]["observations"].append({
            "price": float(row["price"]), "resolution": float(row["resolution"]),
            "at": _epoch(str(row["at"])), "resolved_at": _epoch(str(row["resolved_at"])),
            "role": str(row["role"]), "session": str(row["session_date"]),
        })
    for level in by_id.values():
        if int(level["fit"]["count"]) != len(level["observations"]):
            raise ValueError("Prior V7 fit count differs from its observations")
    seed = {
        "version": "historical-level-mle-book-1",
        "source_extraction_version": EXTRACTION_VERSION,
        "band_config": _BAND_CONFIG,
        "ticker": ticker, "session": str(pinned["session_date"]),
        "available_at": _epoch(str(pinned["available_at"])),
        "input_policy": (str(pinned["input_policy"]) if by_id else POLICY),
        "retrospective": True,
        "source_checkpoint_hash": str(pinned["source_checkpoint_hash"]),
        "levels": [by_id[level_id] for level_id in sorted(by_id)],
    }
    seed["checkpoint_hash"] = digest(seed)
    return seed
