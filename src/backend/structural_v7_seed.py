"""Read-only reconstruction of a prior-session V7 streaming seed from arte.

The retrospective intervals are available only at their session-end publication
time.  Current-session observations are never read from these tables by Backtest.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, time, timezone
from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
from threading import Lock
from typing import Any, Sequence
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
_SEED_CACHE_MAX_WEIGHT = 64 * 1024 * 1024
_seed_cache: OrderedDict[
    tuple[str, ...], tuple[tuple[dict[str, Any], ...],
                           tuple[dict[str, Any], ...], int]
] = OrderedDict()
_seed_cache_weight = 0
_seed_cache_lock = Lock()


def _seed_cache_key(session: date, row: dict[str, Any]) -> tuple[str, ...]:
    # Coverage is reread before lookup. A V2 rebuild or changed source plan
    # cannot reuse a V1 decoded book with the same ticker and seed date.
    return (session.isoformat(), *(str(row[key]) for key in (
        "ticker", "session_date", "available_at", "state",
        "source_checkpoint_hash", "source_plan_hash", "level_count",
        "observation_count", "input_policy", "source_extraction_version",
        "band_config_hash")))


def _cached_seed(key: tuple[str, ...], *, ticker: str, session: date,
                 pinned: dict[str, Any]) -> dict[str, Any] | None:
    with _seed_cache_lock:
        entry = _seed_cache.get(key)
        if entry is None:
            return None
        _seed_cache.move_to_end(key)
    # _assemble_seed reads but never mutates the private cached source rows.
    # Each caller gets fresh mutable levels and observations to hand to V7.
    return _assemble_seed(ticker, session, pinned, entry[0], entry[1])


def _remember_seed_rows(key: tuple[str, ...], *,
                        levels: list[dict[str, Any]],
                        observations: list[dict[str, Any]]) -> None:
    global _seed_cache_weight
    # Conservative bounded approximation for Python dictionaries and strings.
    weight = 2048 + 2048 * len(levels) + 1024 * len(observations)
    if weight > _SEED_CACHE_MAX_WEIGHT:
        return
    with _seed_cache_lock:
        if key in _seed_cache:
            return
        while _seed_cache and _seed_cache_weight + weight > _SEED_CACHE_MAX_WEIGHT:
            _, (_, _, evicted_weight) = _seed_cache.popitem(last=False)
            _seed_cache_weight -= evicted_weight
        _seed_cache[key] = (tuple(levels), tuple(observations), weight)
        _seed_cache_weight += weight


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
    missing_coverage: list[str] = []
    duplicate_coverage = 0
    unexpected_coverage: list[str] = []
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
                missing_coverage.extend(f"{day}:{ticker}" for ticker in sorted(set(batch) - set(found)))
                unexpected_coverage.extend(f"{day}:{ticker}" for ticker in sorted(set(found) - set(batch)))
                duplicate_coverage += len(rows) - len(found)
                continue
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
    if missing_coverage or duplicate_coverage or unexpected_coverage:
        raise ValueError(
            "V7 prior coverage is missing or duplicated: "
            f"missing={len(missing_coverage)} {missing_coverage[:128]}, "
            f"duplicates={duplicate_coverage}, unexpected={unexpected_coverage[:128]}"
        )
    if len(plan_hashes) == 1:
        catalog_hash = next(iter(plan_hashes))
    else:
        from src.trading_runtime.structural_v7_lineage import certified_ticker_lineage
        sources: dict[str, str] = {}
        for unit in units:
            ticker, source = str(unit["ticker"]), str(unit["source_plan_hash"])
            if ticker in sources and sources[ticker] != source:
                raise ValueError("V7 ticker changes source campaigns across sessions")
            sources[ticker] = source
        lineage = certified_ticker_lineage(client, tickers=tuple(sorted(sources)))
        parents = {str(row["parent_source_plan_hash"]) for row in lineage}
        if len(parents) != 1:
            raise ValueError("V7 supplement lineage has no single certified parent")
        parent = next(iter(parents))
        expected = {ticker: source for ticker, source in sources.items()
                    if source != parent}
        actual = {str(row["ticker"]): str(row["supplement_source_plan_hash"])
                  for row in lineage}
        if (parent not in plan_hashes or not expected or actual != expected
                or set(actual.values()) | {parent} != plan_hashes):
            raise ValueError("V7 supplement ticker membership differs from coverage")
        catalog_hash = sha256(json.dumps({
            "parent": parent, "supplements": sorted(actual.items()),
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if len(catalog_hash) != 64 or any(len(value) != 64 for value in plan_hashes):
        raise ValueError("V7 source campaign hash is invalid")
    if len(policies) > 1:
        raise ValueError("V7 prior seeds mix provisional and filtered inputs")
    token = sha256(json.dumps(units, sort_keys=True, separators=(",", ":"),
                              allow_nan=False).encode()).hexdigest()
    return CertifiedSeedPlan(market.build_id, catalog_hash, tuple(units), token,
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


def split_evidence(client: Any, *, ticker: str, seed_session: date,
                   session: date) -> list[dict[str, Any]]:
    """Read split actions known at 04:00 ET, between seed and target session."""
    if seed_session >= session:
        raise ValueError("V7 split evidence requires a preceding seed session")
    rows = _rows(client,
        "SELECT execution_date,split_from,split_to,inserted_at "
        "FROM q_live.market_stock_split_v1 FINAL "
        f"WHERE provider_ticker={_literal(ticker)} "
        f"AND execution_date>toDate({_literal(seed_session.isoformat())}) "
        f"AND execution_date<=toDate({_literal(session.isoformat())}) "
        f"AND inserted_at<=toDateTime64({_literal(_cutoff(session))},9,'UTC') "
        "ORDER BY execution_date,inserted_at FORMAT JSONEachRow")
    return _decode_split_evidence(rows, ticker=ticker, seed_session=seed_session,
                                  session=session)


def _decode_split_evidence(rows: list[dict[str, Any]], *, ticker: str,
                           seed_session: date, session: date) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        day = str(row["execution_date"])
        from_value = float(row["split_from"])
        to_value = float(row["split_to"])
        if (not all(isfinite(value) and value > 0 for value in (from_value, to_value))
                or not seed_session < date.fromisoformat(day) <= session):
            raise ValueError(f"Invalid causal V7 split evidence for {ticker} on {day}")
        previous = unique.get(day)
        if previous is not None and (from_value, to_value) != (
            float(previous["split_from"]), float(previous["split_to"])
        ):
            raise ValueError(f"Conflicting causal V7 split ratios for {ticker} on {day}")
        if previous is None or str(row["inserted_at"]) > str(previous["inserted_at"]):
            unique[day] = dict(row)
    return [unique[day] for day in sorted(unique)]


def split_evidence_batch(client: Any, *, seed_sessions: dict[str, date],
                         session: date) -> dict[str, list[dict[str, Any]]]:
    """Read split evidence for a seed batch in one causal SELECT."""
    tickers = tuple(seed_sessions)
    if (not isinstance(session, date) or not 1 <= len(tickers) <= 8
            or any(not isinstance(ticker, str) or not ticker
                   or not isinstance(day, date) or day >= session
                   for ticker, day in seed_sessions.items())):
        raise ValueError("V7 split batch requires one to eight preceding seeds")
    names = ",".join(_literal(ticker) for ticker in tickers)
    earliest = min(seed_sessions.values())
    rows = _rows(client,
        "SELECT provider_ticker,execution_date,split_from,split_to,inserted_at "
        "FROM q_live.market_stock_split_v1 FINAL "
        f"WHERE provider_ticker IN ({names}) "
        f"AND execution_date>toDate({_literal(earliest.isoformat())}) "
        f"AND execution_date<=toDate({_literal(session.isoformat())}) "
        f"AND inserted_at<=toDateTime64({_literal(_cutoff(session))},9,'UTC') "
        "ORDER BY provider_ticker,execution_date,inserted_at FORMAT JSONEachRow")
    grouped: dict[str, list[dict[str, Any]]] = {ticker: [] for ticker in tickers}
    for row in rows:
        ticker = str(row.get("provider_ticker") or "")
        if ticker not in grouped:
            raise ValueError("V7 split batch returned an unrequested ticker")
        if date.fromisoformat(str(row["execution_date"])) > seed_sessions[ticker]:
            grouped[ticker].append({key: row[key] for key in
                ("execution_date", "split_from", "split_to", "inserted_at")})
    return {ticker: _decode_split_evidence(grouped[ticker], ticker=ticker,
                seed_session=seed_sessions[ticker], session=session)
            for ticker in tickers}


def load_seed(client: Any, *, ticker: str, session: date,
              coverage: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load one complete typed seed; any missing level/observation fails closed."""
    pinned = coverage or preceding_coverage(client, ticker=ticker, session=session)
    current = preceding_coverage(client, ticker=ticker, session=session)
    if any(current.get(key) != pinned.get(key) for key in
           ("session_date", "available_at", "source_checkpoint_hash",
            "source_plan_hash", "level_count", "observation_count",
            "input_policy")):
        raise ValueError("Prior V7 coverage changed after preflight")
    cutoff = _literal(_cutoff(session))
    predicate = (f"ticker={_literal(ticker)} AND valid_from<=toDateTime64({cutoff},9,'UTC') "
                 f"AND (isNull(valid_to) OR valid_to>toDateTime64({cutoff},9,'UTC'))")
    levels = _rows(client, f"SELECT * FROM {_LEVELS} FINAL WHERE {predicate} "
                   "ORDER BY level_id FORMAT JSONEachRow")
    observations = _rows(client, f"SELECT * FROM {_OBSERVATIONS} FINAL WHERE {predicate} "
                         "ORDER BY level_id,observation_id FORMAT JSONEachRow")
    return _assemble_seed(ticker, session, pinned, levels, observations)


def _assemble_seed(ticker: str, session: date, pinned: dict[str, Any],
                   levels: Sequence[dict[str, Any]],
                   observations: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """One decoder for single and batched reads; preserve the seed digest."""
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


def load_seeds_batch(client: Any, *, tickers: tuple[str, ...], session: date,
                     coverage: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Recheck coverage, then reuse or decode at most eight prior books.

    Immutable certified books may be reused in one app process. Every run
    receives a private seed; current coverage is still checked before reuse.
    """
    if (not isinstance(session, date) or type(tickers) is not tuple
            or not 1 <= len(tickers) <= 8 or len(set(tickers)) != len(tickers)
            or any(not isinstance(ticker, str) or not ticker for ticker in tickers)
            or set(coverage) != set(tickers)):
        raise ValueError("V7 batch requires one to eight distinct certified tickers")
    names = ",".join(_literal(ticker) for ticker in tickers)
    cutoff = _literal(_cutoff(session))
    current_rows = _rows(client,
        f"SELECT * FROM {_COVERAGE} FINAL WHERE ticker IN ({names}) "
        f"AND available_at<=toDateTime64({cutoff},9,'UTC') "
        "ORDER BY ticker,available_at DESC LIMIT 1 BY ticker FORMAT JSONEachRow")
    current = {str(row["ticker"]): row for row in current_rows}
    if len(current_rows) != len(tickers) or set(current) != set(tickers):
        raise ValueError("V7 batch coverage changed after preflight")
    for ticker in tickers:
        _validate_coverage(current[ticker], ticker=ticker, session=session)
        if any(current[ticker].get(key) != coverage[ticker].get(key) for key in
               ("session_date", "available_at", "source_checkpoint_hash",
                "source_plan_hash", "level_count", "observation_count",
                "input_policy")):
            raise ValueError("Prior V7 coverage changed after preflight")
    keys = {ticker: _seed_cache_key(session, current[ticker]) for ticker in tickers}
    seeds = {ticker: seed for ticker in tickers
             if (seed := _cached_seed(keys[ticker], ticker=ticker,
                                     session=session,
                                     pinned=coverage[ticker])) is not None}
    missing = tuple(ticker for ticker in tickers if ticker not in seeds)
    if not missing:
        return {ticker: seeds[ticker] for ticker in tickers}
    missing_names = ",".join(_literal(ticker) for ticker in missing)
    predicate = (f"ticker IN ({missing_names}) AND valid_from<=toDateTime64({cutoff},9,'UTC') "
                 f"AND (isNull(valid_to) OR valid_to>toDateTime64({cutoff},9,'UTC'))")
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {
        ticker: {"levels": [], "observations": []} for ticker in missing}
    for table, order, family in (
        (_LEVELS, "ticker,level_id", "levels"),
        (_OBSERVATIONS, "ticker,level_id,observation_id", "observations"),
    ):
        expected = sum(int(coverage[ticker]["level_count" if family == "levels"
                            else "observation_count"]) for ticker in missing)
        for row in _rows(client, f"SELECT * FROM {table} FINAL WHERE {predicate} "
                         f"ORDER BY {order} LIMIT {expected + 1} FORMAT JSONEachRow"):
            ticker = str(row.get("ticker") or "")
            if ticker not in grouped:
                raise ValueError("V7 batch returned an unrequested ticker")
            grouped[ticker][family].append(row)
    for ticker in missing:
        seed = _assemble_seed(ticker, session, coverage[ticker],
                              grouped[ticker]["levels"],
                              grouped[ticker]["observations"])
        seeds[ticker] = seed
        _remember_seed_rows(keys[ticker], levels=grouped[ticker]["levels"],
                            observations=grouped[ticker]["observations"])
    return {ticker: seeds[ticker] for ticker in tickers}
