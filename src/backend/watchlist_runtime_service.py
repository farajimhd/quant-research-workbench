from __future__ import annotations

import os
import threading
from collections import deque
from datetime import UTC, datetime, time, timedelta
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from src.backend.daily_session_bars import daily_session_trade_bars_relation_sql
from src.backend.qmd_gateway_client import qmd_delete_json, qmd_put_json
from src.trading_runtime.watchlist_resolver import (
    SOURCE_FIELDS,
    classify_watchlist_row,
    resolve_watchlist_membership,
)


REFERENCE_CACHE_SECONDS = 60.0
MEMBERSHIP_HISTORY_LIMIT = 10_000
NEW_YORK = ZoneInfo("America/New_York")
_REFERENCE_LOCK = threading.RLock()
_REFERENCE_CACHE: dict[str, dict[str, Any]] = {}
_REFERENCE_CACHE_AT = 0.0


class WatchlistRuntime:
    """Own current Watchlist membership and its append-only change projection."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._members: dict[str, dict[str, dict[str, Any]]] = {}
        self._history: deque[dict[str, Any]] = deque(maxlen=MEMBERSHIP_HISTORY_LIMIT)
        self._published_targets: set[str] = set()
        self._hydrated = False

    def resolve(
        self,
        configuration: dict[str, Any],
        candidates: list[dict[str, Any]],
        *,
        as_of: datetime | None = None,
        publish_targets: bool = True,
        journal: Any | None = None,
    ) -> dict[str, Any]:
        as_of = (as_of or datetime.now(UTC)).astimezone(UTC)
        discovery = dict(configuration.get("market_discovery") or {})
        rule_sets = list(discovery.get("rule_sets") or [])
        calculations = {
            str(row.get("capability_id") or ""): row
            for row in dict(discovery.get("core_scan") or {}).get("calculations") or []
        }
        normalized_candidates = [normalize_watchlist_candidate(row) for row in candidates]
        snapshots: list[dict[str, Any]] = []
        target_errors: list[dict[str, str]] = []
        desired_strategy_targets: set[str] = set()
        with self._lock:
            if journal is not None and not self._hydrated:
                self._hydrate(journal)
            for watchlist in discovery.get("watchlists") or []:
                watchlist_id = str(watchlist.get("watchlist_id") or "").strip()
                if not watchlist_id:
                    continue
                enabled = bool(watchlist.get("enabled", True)) and str(
                    watchlist.get("availability") or "available"
                ) == "available"
                resolved = (
                    resolve_watchlist_membership(watchlist, rule_sets, normalized_candidates)
                    if enabled
                    else []
                )
                current = {
                    str(row.get("ticker") or "").upper(): {
                        **row,
                        "watchlist_id": watchlist_id,
                        "confirmed_at": as_of.isoformat(),
                        "rank": rank,
                        "expires_at": membership_expiry_at(watchlist, as_of),
                    }
                    for rank, row in enumerate(resolved, start=1)
                    if str(row.get("ticker") or "").strip()
                }
                previous = self._members.get(watchlist_id, {})
                current = retain_unconfirmed_members(
                    watchlist,
                    previous,
                    current,
                    as_of=as_of,
                    enabled=enabled,
                )
                events = membership_change_events(
                    watchlist,
                    previous,
                    current,
                    as_of=as_of,
                )
                self._members[watchlist_id] = current
                for event in events:
                    self._history.append(event)
                    if journal is not None:
                        journal.append(
                            run_id=f"watchlist:{watchlist_id}",
                            category="watchlist_membership",
                            entity_type="watchlist_member",
                            entity_id=f"{watchlist_id}:{event['ticker']}",
                            event_time=as_of,
                            payload=event,
                        )
                capabilities, timeframes = focused_target_contract(watchlist, calculations)
                if publish_targets:
                    try:
                        if current and capabilities:
                            publish_watchlist_target(
                                watchlist_id,
                                sorted(current),
                                capabilities,
                                timeframes,
                                ttl_ms=int(watchlist.get("membership_ttl_ms") or 300_000),
                            )
                            self._published_targets.add(f"watchlist:{watchlist_id}")
                        elif f"watchlist:{watchlist_id}" in self._published_targets:
                            publish_watchlist_target(
                                watchlist_id, [], [], [], ttl_ms=1_000
                            )
                            self._published_targets.discard(f"watchlist:{watchlist_id}")
                    except Exception as exc:
                        target_errors.append({"watchlist_id": watchlist_id, "error": str(exc)})
                strategy_targets = strategy_target_contracts(configuration, watchlist_id)
                if publish_targets:
                    for target in strategy_targets:
                        target_id = f"strategy:{target['run_plan_id']}"
                        desired_strategy_targets.add(target_id)
                        try:
                            publish_computation_target(
                                target_id,
                                sorted(current),
                                target["capabilities"],
                                target["timeframes"],
                                owner="backend.strategy_runtime",
                                scope="strategy_run",
                                ttl_ms=int(watchlist.get("membership_ttl_ms") or 300_000),
                            )
                            if current and target["capabilities"]:
                                self._published_targets.add(target_id)
                            else:
                                self._published_targets.discard(target_id)
                        except Exception as exc:
                            target_errors.append({
                                "watchlist_id": watchlist_id,
                                "run_plan_id": target["run_plan_id"],
                                "error": str(exc),
                            })
                snapshots.append(
                    {
                        "watchlist_id": watchlist_id,
                        "name": str(watchlist.get("name") or watchlist_id),
                        "enabled": enabled,
                        "member_count": len(current),
                        "members": list(current.values()),
                        "focused_capabilities": capabilities,
                        "focused_timeframes": timeframes,
                        "strategy_target_ids": [
                            f"strategy:{target['run_plan_id']}" for target in strategy_targets
                        ],
                        "events": events,
                    }
                )
            if publish_targets:
                stale_strategy_targets = {
                    target_id
                    for target_id in self._published_targets
                    if target_id.startswith("strategy:")
                    and target_id not in desired_strategy_targets
                }
                for target_id in stale_strategy_targets:
                    try:
                        publish_computation_target(
                            target_id,
                            [],
                            [],
                            [],
                            owner="backend.strategy_runtime",
                            scope="strategy_run",
                            ttl_ms=1_000,
                        )
                        self._published_targets.discard(target_id)
                    except Exception as exc:
                        target_errors.append({
                            "watchlist_id": "*",
                            "run_plan_id": target_id.removeprefix("strategy:"),
                            "error": str(exc),
                        })
        return {
            "as_of": as_of.isoformat(),
            "watchlist_count": len(snapshots),
            "member_count": sum(row["member_count"] for row in snapshots),
            "watchlists": snapshots,
            "target_errors": target_errors,
            "status": "ready" if not target_errors else "degraded",
        }

    def _hydrate(self, journal: Any) -> None:
        for record in journal.watchlist_membership_records(
            limit=MEMBERSHIP_HISTORY_LIMIT
        ):
            event = dict(record.payload)
            watchlist_id = str(event.get("watchlist_id") or "")
            ticker = str(event.get("ticker") or "").upper()
            if not watchlist_id or not ticker:
                continue
            members = self._members.setdefault(watchlist_id, {})
            if str(event.get("event") or "") == "added":
                members[ticker] = {
                    "ticker": ticker,
                    "watchlist_id": watchlist_id,
                    "membership_reason": str(event.get("reason") or "restored membership"),
                    "confirmed_at": str(event.get("available_at") or ""),
                    "expires_at": event.get("expires_at"),
                }
            else:
                members.pop(ticker, None)
            self._history.append(event)
        self._hydrated = True

    def seed_focused_targets(
        self,
        configuration: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Lease bounded candidates for rules that require focused evidence.

        This breaks the circular dependency between membership rules such as a
        VWAP breakout and the indicator that is intentionally absent from Core
        Scan. Final resolution replaces this seed with exact membership.
        """
        discovery = dict(configuration.get("market_discovery") or {})
        rule_sets = {
            str(row.get("rule_set_id") or ""): row
            for row in discovery.get("rule_sets") or []
        }
        calculations = {
            str(row.get("capability_id") or ""): row
            for row in dict(discovery.get("core_scan") or {}).get("calculations") or []
        }
        normalized = [normalize_watchlist_candidate(row) for row in candidates]
        normalized.sort(
            key=lambda row: numeric_value(row, "liquidity_rank") or float("-inf"),
            reverse=True,
        )
        seeded: list[dict[str, Any]] = []
        with self._lock:
            for watchlist in discovery.get("watchlists") or []:
                watchlist_id = str(watchlist.get("watchlist_id") or "")
                if (
                    not watchlist_id
                    or not bool(watchlist.get("enabled", True))
                    or str(watchlist.get("availability") or "available") != "available"
                    or not watchlist_requires_focused_evidence(watchlist, rule_sets)
                ):
                    continue
                capabilities, timeframes = focused_target_contract(watchlist, calculations)
                if not capabilities:
                    continue
                seed_limit = max(1, int(watchlist.get("maximum_size") or 1)) * 5
                tickers = {
                    str(row.get("ticker") or "").upper()
                    for row in normalized[:seed_limit]
                    if str(row.get("ticker") or "").strip()
                }
                tickers.update(self._members.get(watchlist_id, {}))
                tickers.update(
                    str(value).strip().upper()
                    for value in watchlist.get("manual_inclusions") or []
                    if str(value).strip()
                )
                excluded = {
                    str(value).strip().upper()
                    for value in watchlist.get("manual_exclusions") or []
                    if str(value).strip()
                }
                tickers -= excluded
                publish_watchlist_target(
                    watchlist_id,
                    sorted(tickers),
                    capabilities,
                    timeframes,
                    ttl_ms=int(watchlist.get("membership_ttl_ms") or 300_000),
                )
                self._published_targets.add(watchlist_id)
                seeded.append(
                    {
                        "watchlist_id": watchlist_id,
                        "candidate_count": len(tickers),
                        "capabilities": capabilities,
                        "timeframes": timeframes,
                    }
                )
        return seeded

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            watchlists = [
                {
                    "watchlist_id": watchlist_id,
                    "member_count": len(members),
                    "members": list(members.values()),
                }
                for watchlist_id, members in sorted(self._members.items())
            ]
            return {
                "as_of": datetime.now(UTC).isoformat(),
                "watchlist_count": len(watchlists),
                "member_count": sum(row["member_count"] for row in watchlists),
                "watchlists": watchlists,
                "history": list(self._history),
                "history_count": len(self._history),
                "status": "ready" if self._hydrated else "awaiting_first_resolution",
            }


WATCHLIST_RUNTIME = WatchlistRuntime()


def normalize_watchlist_candidate(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    ticker = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
    last_price = numeric_value(row, "last_price", "last_close", "current_open", "last")
    previous_close = numeric_value(row, "previous_close")
    result.update(
        {
            "ticker": ticker,
            "last_price": last_price,
            "volume": numeric_value(row, "volume", "last_day_volume_so_far", "day_volume"),
            "liquidity_rank": numeric_value(row, "liquidity_rank", "live_priority"),
            "vwap": numeric_value(row, "vwap", "last_vwap"),
        }
    )
    if row.get("change_pct") is not None:
        result["change_pct"] = numeric_value(row, "change_pct")
    elif last_price and previous_close and previous_close > 0:
        result["change_pct"] = (last_price / previous_close - 1) * 100
    return classify_watchlist_row(result)


def membership_change_events(
    watchlist: dict[str, Any],
    previous: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
    *,
    as_of: datetime,
) -> list[dict[str, Any]]:
    watchlist_id = str(watchlist.get("watchlist_id") or "")
    events: list[dict[str, Any]] = []
    for ticker in sorted(current.keys() - previous.keys()):
        row = current[ticker]
        events.append(
            {
                "event": "added",
                "watchlist_id": watchlist_id,
                "ticker": ticker,
                "event_time": as_of.isoformat(),
                "available_at": as_of.isoformat(),
                "reason": str(row.get("membership_reason") or "rules passed"),
                "ranking_field": str(watchlist.get("ranking_field") or ""),
                "ranking_value": row.get(
                    SOURCE_FIELDS.get(
                        str(watchlist.get("ranking_field") or ""),
                        str(watchlist.get("ranking_field") or ""),
                    )
                ),
                "rank": row.get("rank"),
                "expires_at": row.get("expires_at"),
            }
        )
    for ticker in sorted(previous.keys() - current.keys()):
        expiry = parse_datetime(previous[ticker].get("expires_at"))
        events.append(
            {
                "event": "expired" if expiry is not None and expiry <= as_of else "removed",
                "watchlist_id": watchlist_id,
                "ticker": ticker,
                "event_time": as_of.isoformat(),
                "available_at": as_of.isoformat(),
                "reason": (
                    "membership expiry reached without reconfirmation"
                    if expiry is not None and expiry <= as_of
                    else "manual exclusion, disabled Watchlist, or removal policy"
                ),
            }
        )
    return events


def retain_unconfirmed_members(
    watchlist: dict[str, Any],
    previous: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
    *,
    as_of: datetime,
    enabled: bool,
) -> dict[str, dict[str, Any]]:
    if not enabled:
        return current
    policy = str(watchlist.get("membership_expiry") or "end_of_trading_day")
    excluded = {
        str(value).strip().upper()
        for value in watchlist.get("manual_exclusions") or []
        if str(value).strip()
    }
    for ticker, member in previous.items():
        if ticker in current or ticker in excluded:
            continue
        expiry = parse_datetime(member.get("expires_at"))
        if policy == "never" or (expiry is not None and expiry > as_of):
            current[ticker] = dict(member)
    return current


def membership_expiry_at(watchlist: dict[str, Any], as_of: datetime) -> str | None:
    policy = str(watchlist.get("membership_expiry") or "end_of_trading_day")
    if policy == "never":
        return None
    if policy == "time_to_live":
        ttl_ms = max(1, int(watchlist.get("membership_ttl_ms") or 0))
        return (as_of + timedelta(milliseconds=ttl_ms)).isoformat()
    local = as_of.astimezone(NEW_YORK)
    expiry = datetime.combine(local.date(), time(20, 0), NEW_YORK)
    if expiry <= local:
        expiry += timedelta(days=1)
    return expiry.astimezone(UTC).isoformat()


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def focused_target_contract(
    watchlist: dict[str, Any],
    calculations: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str]]:
    capabilities: set[str] = set()
    timeframes: set[str] = set()
    for capability_id in watchlist.get("calculations") or []:
        capability_id = str(capability_id)
        if not capability_id.startswith("qmd.family."):
            continue
        capability = calculations.get(capability_id, {})
        if str(capability.get("availability") or "") not in {
            "implemented",
            "strategy_specific",
        }:
            continue
        capabilities.add(capability_id.removeprefix("qmd.family."))
        timeframes.update(
            str(value)
            for value in capability.get("selected_timeframes") or []
            if str(value) not in {"", "session", "1d", "settlement", "event", "filing"}
        )
    return sorted(capabilities), sorted(timeframes)


def watchlist_requires_focused_evidence(
    watchlist: dict[str, Any], rule_sets: dict[str, dict[str, Any]]
) -> bool:
    rule_ids = [
        *list(watchlist.get("inclusion_rule_sets") or []),
        *list(watchlist.get("exclusion_rule_sets") or []),
    ]
    for rule_id in rule_ids:
        rule = rule_sets.get(str(rule_id), {})
        for condition in rule.get("conditions") or []:
            sources = {
                str(condition.get("left_source_id") or ""),
                str(condition.get("right_source_id") or ""),
            }
            if any(source.startswith("indicator.") for source in sources):
                return True
    return False


def publish_watchlist_target(
    watchlist_id: str,
    tickers: list[str],
    capabilities: list[str],
    timeframes: list[str],
    *,
    ttl_ms: int,
) -> None:
    publish_computation_target(
        f"watchlist:{watchlist_id}",
        tickers,
        capabilities,
        timeframes,
        owner="backend.market_discovery",
        scope="watchlist",
        ttl_ms=ttl_ms,
    )


def publish_computation_target(
    target_id: str,
    tickers: list[str],
    capabilities: list[str],
    timeframes: list[str],
    *,
    owner: str,
    scope: str,
    ttl_ms: int,
) -> None:
    if not tickers or not capabilities:
        qmd_delete_json(f"/computation-targets/{target_id}", timeout=3)
        return
    qmd_put_json(
        "/computation-targets",
        {
            "target_id": target_id,
            "owner": owner,
            "scope": scope,
            "tickers": tickers,
            "capabilities": capabilities,
            "timeframes": timeframes,
            "ttl_seconds": max(1, int(ttl_ms) // 1000),
        },
        timeout=3,
    )


def strategy_target_contracts(
    configuration: dict[str, Any], watchlist_id: str
) -> list[dict[str, Any]]:
    run_plans = dict(configuration.get("run_plans") or {})
    universe_ids = {
        str(universe.get("universe_id") or "")
        for universe in run_plans.get("universes") or []
        if str(universe.get("source") or "") == "watchlist"
        and str(universe.get("scanner_view_id") or "") == watchlist_id
    }
    contracts: list[dict[str, Any]] = []
    for plan in run_plans.get("plans") or []:
        run_plan_id = str(plan.get("run_plan_id") or "").strip()
        if (
            not run_plan_id
            or not bool(plan.get("enabled", True))
            or str(plan.get("universe_id") or "") not in universe_ids
            or not ({"paper", "live"} & set(plan.get("allowed_environments") or []))
        ):
            continue
        qmd_dependencies = [
            row
            for row in plan.get("observation_dependencies") or []
            if str(row.get("producer") or "") == "qmd"
            and str(row.get("capability_key") or "")
        ]
        capabilities = sorted({str(row["capability_key"]) for row in qmd_dependencies})
        timeframes = sorted({
            str(timeframe).lower()
            for row in qmd_dependencies
            for timeframe in row.get("timeframes") or []
            if str(timeframe).strip()
        })
        if capabilities:
            contracts.append({
                "run_plan_id": run_plan_id,
                "capabilities": capabilities,
                "timeframes": timeframes,
            })
    return contracts


def live_market_reference_projection(
    as_of: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    global _REFERENCE_CACHE, _REFERENCE_CACHE_AT
    now = monotonic()
    with _REFERENCE_LOCK:
        if _REFERENCE_CACHE and now - _REFERENCE_CACHE_AT < REFERENCE_CACHE_SECONDS:
            return _REFERENCE_CACHE
    cutoff = (as_of or datetime.now(UTC)).astimezone(UTC)
    from src.backend.historical_scanner_service import historical_scanner_reference_projection

    projection = historical_scanner_reference_projection(cutoff)
    source_database = os.environ.get(
        "QMD_HISTORY_CLICKHOUSE_DATABASE", "market_sip_compact"
    ).strip()
    daily_relation = daily_session_trade_bars_relation_sql(
        database=source_database,
        start_date=cutoff.date() - timedelta(days=45),
        end_date=cutoff.date(),
        as_of=cutoff,
    )
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
    )
    daily_rows = client.execute(
        f"""
        SELECT
            upper(sym) AS ticker,
            argMax(close, session_date) AS previous_close,
            avg(size_sum) AS average_daily_volume
        FROM ({daily_relation})
        GROUP BY ticker
        FORMAT JSONEachRow
        """
    )
    import json

    for line in daily_rows.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        ticker = str(row.get("ticker") or "").upper()
        if ticker:
            projection.setdefault(ticker, {}).update(
                {
                    "previous_close": row.get("previous_close"),
                    "average_daily_volume": row.get("average_daily_volume"),
                    "reference_available_at": cutoff.isoformat(),
                }
            )
    with _REFERENCE_LOCK:
        _REFERENCE_CACHE = projection
        _REFERENCE_CACHE_AT = now
    return projection


def enrich_core_scanner_rows(
    rows: list[dict[str, Any]],
    reference_projection: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        normalize_watchlist_candidate(
            {**row, **reference_projection.get(str(row.get("ticker") or "").upper(), {})}
        )
        for row in rows
    ]


def numeric_value(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def resolve_historical_watchlist(
    configuration: dict[str, Any],
    watchlist_id: str,
    *,
    as_of: datetime,
) -> dict[str, Any]:
    """Resolve one Watchlist from causal historical scanner products."""
    if as_of.tzinfo is None:
        raise ValueError("historical Watchlist as_of must be timezone-aware")
    discovery = dict(configuration.get("market_discovery") or {})
    watchlist = next(
        (
            row
            for row in discovery.get("watchlists") or []
            if str(row.get("watchlist_id") or "") == watchlist_id
        ),
        None,
    )
    if watchlist is None:
        raise ValueError(f"unknown Watchlist: {watchlist_id}")
    from src.backend.historical_scanner_service import (
        historical_scanner_fundamental_projection,
        historical_scanner_reference_projection,
        historical_scanner_snapshot,
        historical_scanner_technical_projection,
    )

    rows, scanner_meta = historical_scanner_snapshot(as_of, lookback_minutes=15)
    if not rows:
        return {
            "as_of": as_of.astimezone(UTC).isoformat(),
            "watchlist_id": watchlist_id,
            "members": [],
            "member_count": 0,
            "status": str(scanner_meta.get("status") or "building"),
            "scanner": scanner_meta,
        }
    rule_by_id = {
        str(row.get("rule_set_id") or ""): row
        for row in discovery.get("rule_sets") or []
    }
    sources = watchlist_rule_sources(watchlist, rule_by_id)
    calculation_windows = sorted(
        {
            str(condition.get("left_timeframe") or "")
            for rule_id in [
                *list(watchlist.get("inclusion_rule_sets") or []),
                *list(watchlist.get("exclusion_rule_sets") or []),
            ]
            for condition in rule_by_id.get(str(rule_id), {}).get("conditions") or []
            if str(condition.get("left_timeframe") or "")
            in {"100ms", "1s", "5s", "10s", "30s", "1m", "5m", "15m", "30m", "1h", "extended_session", "regular_session"}
        }
    )
    reference = historical_scanner_reference_projection(as_of)
    technical, technical_meta = historical_scanner_technical_projection(
        as_of, calculation_windows=calculation_windows
    )
    fundamentals = (
        historical_scanner_fundamental_projection(as_of)
        if any(source.startswith("fundamental.") for source in sources)
        else {}
    )
    candidates: list[dict[str, Any]] = []
    for raw in rows:
        ticker = str(raw.get("ticker") or raw.get("symbol") or "").upper()
        merged = {
            **raw,
            **reference.get(ticker, {}),
            **technical.get(ticker, {}),
            **fundamentals.get(ticker, {}),
        }
        for window in calculation_windows:
            if merged.get("relative_volume") is None:
                merged["relative_volume"] = merged.get(
                    f"technical__relative_volume__{window}"
                )
            if merged.get("vwap") is None:
                merged["vwap"] = merged.get(
                    f"technical__vwap__{window}__hlc3"
                ) or merged.get(f"technical__vwap__{window}__trade_price")
        candidates.append(normalize_watchlist_candidate(merged))
    members = resolve_watchlist_membership(
        watchlist, discovery.get("rule_sets") or [], candidates
    )
    scanner_ready = bool(scanner_meta.get("complete_universe")) and str(
        scanner_meta.get("status") or ""
    ) == "ready"
    return {
        "as_of": as_of.astimezone(UTC).isoformat(),
        "watchlist_id": watchlist_id,
        "members": members,
        "member_count": len(members),
        "status": "ready" if scanner_ready else str(scanner_meta.get("status") or "partial"),
        "scanner": scanner_meta,
        "technical": technical_meta,
    }


def watchlist_rule_sources(
    watchlist: dict[str, Any], rule_sets: dict[str, dict[str, Any]]
) -> set[str]:
    result: set[str] = set()
    for rule_id in [
        *list(watchlist.get("inclusion_rule_sets") or []),
        *list(watchlist.get("exclusion_rule_sets") or []),
    ]:
        for condition in rule_sets.get(str(rule_id), {}).get("conditions") or []:
            result.add(str(condition.get("left_source_id") or ""))
            result.add(str(condition.get("right_source_id") or ""))
    return {value for value in result if value}
