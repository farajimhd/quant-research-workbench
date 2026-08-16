from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import deque
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from src.backend.query_plans.market_daily_bars_v1 import (
    daily_market_reference_projection,
)
from src.backend.bounded_cache import BoundedTtlCache
from src.backend.qmd_gateway_client import qmd_delete_json, qmd_put_json
from src.backend.data_field_contracts import (
    compile_data_field_plan,
    data_field_output_index,
    field_instance_ref,
    interval_expression,
    project_composition_data_field_columns,
    project_data_field_outputs,
)
from src.request_context import causal_identity
from src.trading_runtime.watchlist_resolver import (
    SOURCE_FIELDS,
    classify_watchlist_row,
    evaluate_rule_set_result,
    evaluate_watchlist_candidate,
    rank_watchlist_membership,
    resolve_watchlist_membership,
)


REFERENCE_CACHE_SECONDS = 60.0
MEMBERSHIP_HISTORY_LIMIT = 10_000
WATCHLIST_MEMBER_IDENTITY_FIELDS = (
    "symbol_id",
    "listing_id",
    "security_id",
    "issuer_id",
)
NEW_YORK = ZoneInfo("America/New_York")
_REFERENCE_CACHE = BoundedTtlCache[str, dict[str, dict[str, Any]]](
    max_entries=4,
    ttl_seconds=REFERENCE_CACHE_SECONDS,
    contract_revision="watchlist-reference-projection.v1",
)
_LIVE_REFERENCE_LOCK = threading.RLock()
_LIVE_REFERENCE_PROJECTION: dict[str, dict[str, Any]] | None = None
_LIVE_REFERENCE_LOADED_AT: datetime | None = None
_LIVE_REFERENCE_REFRESHING = False
_LIVE_REFERENCE_REFRESH_ERROR = ""


class WatchlistRuntime:
    """Own current Watchlist membership and its append-only change projection."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._members: dict[str, dict[str, dict[str, Any]]] = {}
        self._history: deque[dict[str, Any]] = deque(maxlen=MEMBERSHIP_HISTORY_LIMIT)
        self._published_targets: set[str] = set()
        self._eligible: dict[str, dict[str, dict[str, Any]]] = {}
        self._candidate_fingerprints: dict[str, dict[str, str]] = {}
        self._watchlist_revisions: dict[str, str] = {}
        self._hydrated = False

    def resolve(
        self,
        configuration: dict[str, Any],
        candidates: list[dict[str, Any]],
        *,
        as_of: datetime | None = None,
        publish_targets: bool = True,
        journal: Any | None = None,
        admissions_by_watchlist: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        as_of = (as_of or datetime.now(UTC)).astimezone(UTC)
        discovery = dict(configuration.get("market_discovery") or {})
        rule_sets = list(discovery.get("rule_sets") or [])
        calculations = {
            str(row.get("capability_id") or ""): row
            for row in discovery.get("calculation_catalog") or []
        }
        column_sources = {
            str(row.get("column_id") or ""): str(row.get("field_ref") or row.get("source_id") or "")
            for row in discovery.get("column_catalog") or []
        }
        normalized_candidates = project_configured_rule_set_columns(
            configuration,
            project_data_field_outputs(
                [normalize_watchlist_candidate(row) for row in candidates],
                discovery.get("data_fields") or [],
            ),
        )
        candidates_by_ticker = {
            str(row.get("ticker") or "").upper(): row
            for row in normalized_candidates
            if str(row.get("ticker") or "").strip()
        }
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
                recomputed_candidate_count = 0
                if enabled:
                    resolved, recomputed_candidate_count = self._resolve_incremental(
                        watchlist,
                        rule_sets,
                        candidates_by_ticker,
                    )
                    admitted = [
                        dict(row)
                        for row in (admissions_by_watchlist or {}).get(watchlist_id, [])
                        if str(row.get("ticker") or "").upper()
                        not in {str(value).upper() for value in watchlist.get("manual_exclusions") or []}
                    ]
                    if admitted:
                        by_ticker = {
                            str(row.get("ticker") or "").upper(): row for row in resolved
                        }
                        for row in admitted:
                            by_ticker.setdefault(str(row.get("ticker") or "").upper(), row)
                        resolved = rank_watchlist_membership(
                            watchlist,
                            by_ticker.values(),
                            observed_symbols=by_ticker,
                        )
                else:
                    resolved = []
                    self._eligible.pop(watchlist_id, None)
                    self._candidate_fingerprints.pop(watchlist_id, None)
                    self._watchlist_revisions.pop(watchlist_id, None)
                resolved = project_composition_data_field_columns(
                    resolved,
                    watchlist,
                    discovery.get("column_catalog") or [],
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
                member_fields = watchlist_dependency_fields(watchlist, rule_sets)
                presentation_fields = tuple(
                    sorted(set(member_fields) | {str(value) for value in watchlist.get("columns") or []})
                )
                current = {
                    ticker: compact_watchlist_member(row, presentation_fields)
                    for ticker, row in current.items()
                }
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
                capabilities, timeframes = focused_target_contract(
                    watchlist, rule_sets, calculations, column_sources, discovery.get("data_fields") or []
                )
                if publish_targets:
                    try:
                        if current and capabilities:
                            publish_watchlist_target(
                                watchlist_id,
                                sorted(current),
                                capabilities,
                                timeframes,
                                ttl_ms=int(watchlist.get("membership_ttl_ms") or 300_000),
                                causation_seed=f"{watchlist_id}:{as_of.isoformat()}",
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
                                causation_seed=f"{watchlist_id}:{as_of.isoformat()}",
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
                        "recomputed_candidate_count": recomputed_candidate_count,
                        "candidate_population_count": len(candidates_by_ticker),
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

    def _resolve_incremental(
        self,
        watchlist: dict[str, Any],
        rule_sets: list[dict[str, Any]],
        candidates_by_ticker: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        """Re-evaluate only symbols whose inputs used by this Watchlist changed."""

        watchlist_id = str(watchlist.get("watchlist_id") or "")
        revision = watchlist_resolution_revision(watchlist, rule_sets)
        eligible = self._eligible.setdefault(watchlist_id, {})
        previous_fingerprints = self._candidate_fingerprints.setdefault(
            watchlist_id, {}
        )
        if self._watchlist_revisions.get(watchlist_id) != revision:
            eligible.clear()
            previous_fingerprints.clear()
            self._watchlist_revisions[watchlist_id] = revision
        fields = watchlist_dependency_fields(watchlist, rule_sets)
        current_fingerprints = {
            ticker: watchlist_candidate_fingerprint(row, fields)
            for ticker, row in candidates_by_ticker.items()
        }
        changed = {
            ticker
            for ticker, fingerprint in current_fingerprints.items()
            if previous_fingerprints.get(ticker) != fingerprint
        }
        removed = set(previous_fingerprints) - set(current_fingerprints)
        for ticker in removed:
            eligible.pop(ticker, None)
        for ticker in changed:
            matched = evaluate_watchlist_candidate(
                watchlist,
                rule_sets,
                candidates_by_ticker[ticker],
            )
            if matched is None:
                eligible.pop(ticker, None)
            else:
                eligible[ticker] = matched
        self._candidate_fingerprints[watchlist_id] = current_fingerprints
        return (
            rank_watchlist_membership(
                watchlist,
                eligible.values(),
                observed_symbols=candidates_by_ticker,
            ),
            len(changed) + len(removed),
        )

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
            for row in discovery.get("calculation_catalog") or []
        }
        column_sources = {
            str(row.get("column_id") or ""): str(row.get("field_ref") or row.get("source_id") or "")
            for row in discovery.get("column_catalog") or []
        }
        normalized = project_configured_rule_set_columns(
            configuration,
            project_data_field_outputs(
                [normalize_watchlist_candidate(row) for row in candidates],
                discovery.get("data_fields") or [],
            ),
        )
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
                capabilities, timeframes = focused_target_contract(
                    watchlist, rule_sets, calculations, column_sources, discovery.get("data_fields") or []
                )
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


def project_watchlists_from_candidates(
    configuration: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    as_of: datetime,
    available_fields: set[str] | None = None,
    source_complete: bool,
    source_status: str,
) -> dict[str, Any]:
    """Project every configured Watchlist from one causal scanner snapshot.

    Canvas uses this read-only projection so historical membership shares the
    exact same as-of candidate population as its Scanner container. It must not
    mutate the advancing live runtime, journal membership, or publish targets.
    """
    if as_of.tzinfo is None:
        raise ValueError("Watchlist projection as_of must be timezone-aware")
    as_of = as_of.astimezone(UTC)
    discovery = dict(configuration.get("market_discovery") or {})
    rule_sets = list(discovery.get("rule_sets") or [])
    normalized_candidates = project_configured_rule_set_columns(
        configuration,
        project_data_field_outputs(
            [normalize_watchlist_candidate(row) for row in candidates],
            discovery.get("data_fields") or [],
        ),
    )
    effective_available_fields = None if available_fields is None else set(available_fields)
    snapshots: list[dict[str, Any]] = []
    projection_ready = source_complete and source_status == "ready"
    unresolved_source_status = (
        source_status
        if source_status in {"awaiting_first_resolution", "building", "error", "refreshing"}
        else "partial"
    )
    for watchlist in discovery.get("watchlists") or []:
        watchlist_id = str(watchlist.get("watchlist_id") or "").strip()
        if not watchlist_id:
            continue
        availability = str(watchlist.get("availability") or "available")
        enabled = bool(watchlist.get("enabled", True)) and availability == "available"
        member_fields = watchlist_dependency_fields(watchlist, rule_sets)
        watchlist_ready = projection_ready and (
            effective_available_fields is None
            or set(member_fields).issubset(effective_available_fields)
        )
        resolved = (
            resolve_watchlist_membership(watchlist, rule_sets, normalized_candidates)
            if enabled and watchlist_ready
            else []
        )
        resolved = project_composition_data_field_columns(
            resolved,
            watchlist,
            discovery.get("column_catalog") or [],
        )
        members = [
            compact_watchlist_member(
                {
                    **row,
                    "watchlist_id": watchlist_id,
                    "confirmed_at": as_of.isoformat(),
                    "rank": row.get("rank", rank),
                },
                tuple(sorted(set(member_fields) | {str(value) for value in watchlist.get("columns") or []})),
            )
            for rank, row in enumerate(resolved, start=1)
        ]
        snapshots.append(
            {
                "watchlist_id": watchlist_id,
                "name": str(watchlist.get("name") or watchlist_id),
                "enabled": enabled,
                "availability": availability,
                "member_count": len(members),
                "members": members,
                "candidate_population_count": len(normalized_candidates),
                "status": (
                    "ready"
                    if enabled and watchlist_ready
                    else "disabled"
                    if not enabled
                    else "partial" if projection_ready else unresolved_source_status
                ),
            }
        )
    return {
        "as_of": as_of.isoformat(),
        "watchlist_count": len(snapshots),
        "member_count": sum(row["member_count"] for row in snapshots),
        "watchlists": snapshots,
        "status": (
            "ready"
            if projection_ready and all(row["status"] in {"ready", "disabled"} for row in snapshots)
            else "partial"
            if projection_ready
            else unresolved_source_status
        ),
        "source_complete": source_complete,
        "source": "canvas_scanner_snapshot",
    }


def watchlist_resolution_revision(
    watchlist: dict[str, Any],
    rule_sets: list[dict[str, Any]] | dict[str, dict[str, Any]],
) -> str:
    selected_ids = {
        str(value)
        for key in ("inclusion_rule_sets", "exclusion_rule_sets")
        for value in watchlist.get(key) or []
    }
    payload = {
        "watchlist": watchlist,
        "rule_sets": [
            row
            for row in rule_sets
            if str(row.get("rule_set_id") or "") in selected_ids
        ],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def watchlist_dependency_fields(
    watchlist: dict[str, Any],
    rule_sets: list[dict[str, Any]],
) -> tuple[str, ...]:
    """Return only row fields that can alter eligibility or final rank."""

    selected_ids = {
        str(value)
        for key in ("inclusion_rule_sets", "exclusion_rule_sets")
        for value in watchlist.get(key) or []
    }
    ranking_ref = str(watchlist.get("ranking_field_ref") or "")
    ranking_interval = interval_expression(watchlist.get("ranking_interval"))
    source_ids = {
        str(watchlist.get("ranking_field") or ""),
        field_instance_ref(ranking_ref, ranking_interval)
        if ranking_ref and ranking_interval else "",
    }
    rule_rows = rule_sets.values() if isinstance(rule_sets, dict) else rule_sets
    for rule_set in rule_rows:
        if str(rule_set.get("rule_set_id") or "") not in selected_ids:
            continue
        for condition in rule_set.get("conditions") or []:
            if not bool(condition.get("enabled", True)):
                continue
            source_ids.add(str(condition.get("left_source_id") or ""))
            source_ids.add(str(condition.get("right_source_id") or ""))
            left_ref = str(condition.get("left_field_ref") or "")
            right_ref = str(condition.get("right_field_ref") or "")
            if left_ref and condition.get("left_interval"):
                source_ids.add(field_instance_ref(left_ref, condition.get("left_interval")))
            if right_ref and condition.get("right_interval"):
                source_ids.add(field_instance_ref(right_ref, condition.get("right_interval")))
    fields = {
        SOURCE_FIELDS.get(source_id, source_id)
        for source_id in source_ids
        if source_id
    }
    return tuple(sorted(fields | {"ticker"}))


def watchlist_candidate_fingerprint(
    row: dict[str, Any],
    fields: tuple[str, ...],
) -> str:
    payload = {field: row.get(field) for field in fields}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compact_watchlist_member(
    row: dict[str, Any], dependency_fields: tuple[str, ...]
) -> dict[str, Any]:
    """Keep causal membership evidence without retaining a wide Scanner row."""

    fields = {
        *dependency_fields,
        *WATCHLIST_MEMBER_IDENTITY_FIELDS,
        "watchlist_id",
        "membership_reason",
        "confirmed_at",
        "expires_at",
        "rank",
        "causation_signal_event_id",
        "causation_signal_stream_id",
    }
    return {field: row.get(field) for field in fields if row.get(field) is not None}


def project_configured_rule_set_columns(
    configuration: dict[str, Any],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project selected registered Rule Sets as reusable boolean table columns."""

    discovery = dict(configuration.get("market_discovery") or {})
    selected_column_ids = {
        str(value)
        for value in dict(discovery.get("core_scan") or {}).get("columns") or []
    }
    for watchlist in discovery.get("watchlists") or []:
        selected_column_ids.update(str(value) for value in watchlist.get("columns") or [])
    for stream in discovery.get("signal_streams") or []:
        selected_column_ids.update(str(value) for value in stream.get("columns") or [])
    columns = {
        str(column.get("column_id") or ""): column
        for column in discovery.get("column_catalog") or []
        if str(column.get("column_id") or "") in selected_column_ids
        and str(column.get("source_kind") or "") == "rule_set"
    }
    if not columns:
        return rows
    rule_sets = {
        str(rule_set.get("rule_set_id") or ""): rule_set
        for rule_set in discovery.get("rule_sets") or []
    }
    projected: list[dict[str, Any]] = []
    for row in rows:
        result = dict(row)
        for column_id, column in columns.items():
            result[column_id] = evaluate_rule_set_result(
                rule_sets.get(str(column.get("source_id") or "")),
                row,
            )
        projected.append(result)
    return projected


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
    rule_sets: list[dict[str, Any]] | dict[str, dict[str, Any]],
    calculations: dict[str, dict[str, Any]],
    column_sources: dict[str, str] | None = None,
    data_fields: list[dict[str, Any]] | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve QMD demand from the Watchlist's registered references."""

    capabilities: set[str] = set()
    timeframes: set[str] = set()
    selected_rule_ids = {
        str(value)
        for key in ("inclusion_rule_sets", "exclusion_rule_sets")
        for value in watchlist.get(key) or []
    }
    referenced_sources = {
        str(watchlist.get("ranking_field") or ""),
        str(watchlist.get("ranking_field_ref") or ""),
    }
    ranking_interval = interval_expression(watchlist.get("ranking_interval"))
    if ranking_interval:
        timeframes.add(ranking_interval)
    rule_rows = rule_sets.values() if isinstance(rule_sets, dict) else rule_sets
    for rule_set in rule_rows:
        if str(rule_set.get("rule_set_id") or "") not in selected_rule_ids:
            continue
        for condition in rule_set.get("conditions") or []:
            if not bool(condition.get("enabled", True)):
                continue
            referenced_sources.add(str(condition.get("left_source_id") or ""))
            referenced_sources.add(str(condition.get("right_source_id") or ""))
            referenced_sources.add(str(condition.get("left_field_ref") or ""))
            referenced_sources.add(str(condition.get("right_field_ref") or ""))
            timeframes.update(
                interval_expression(condition.get(key))
                for key in ("left_interval", "right_interval")
                if str(condition.get(key) or "")
            )
    column_sources = column_sources or {}
    referenced_sources.update(
        column_sources.get(str(value), str(value))
        for value in watchlist.get("columns") or []
    )
    timeframes.update(
        interval_expression(value)
        for value in dict(watchlist.get("column_intervals") or {}).values()
        if str(value)
    )
    if data_fields:
        output_index = data_field_output_index(data_fields)
        exact_refs = {
            str((output_index.get(value) or {}).get("field_ref") or value)
            for value in referenced_sources
            if value
        }
        for data_field in data_fields:
            outputs = {
                str(output.get("field_ref") or "")
                for output in data_field.get("outputs") or []
            }
            if not outputs.intersection(exact_refs):
                continue
            recipe_id = str(data_field.get("recipe_id") or "")
            if recipe_id.startswith("qmd.family."):
                capabilities.add(recipe_id.removeprefix("qmd.family."))
            elif (
                recipe_id
                and recipe_id != "registered_projection"
                and str(data_field.get("owner") or "").lower() in {"qmd", "qmd_gateway"}
            ):
                capabilities.add(recipe_id)
            if str(dict(data_field.get("context") or {}).get("dimension_kind") or "") != "interval":
                timeframes.update(
                    str(value)
                    for value in dict(data_field.get("execution") or {}).get("producer_intervals") or []
                    if str(value)
                )
        if capabilities:
            return sorted(capabilities), sorted(value for value in timeframes if value not in {"session", "settlement", "event", "filing", "evaluation"})
    for capability_id, capability in calculations.items():
        outputs = {
            str(value) for value in capability.get("fields") or [] if str(value)
        }
        outputs.add(capability_id)
        if not outputs.intersection(referenced_sources):
            continue
        capability_id = str(capability_id)
        if not capability_id.startswith("qmd.family."):
            continue
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
    return sorted(capabilities), sorted(value for value in timeframes if value not in {"session", "1d", "settlement", "event", "filing", "evaluation"})


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
    causation_seed: object | None = None,
) -> None:
    publish_computation_target(
        f"watchlist:{watchlist_id}",
        tickers,
        capabilities,
        timeframes,
        owner="backend.market_discovery",
        scope="watchlist",
        ttl_ms=ttl_ms,
        causation_seed=causation_seed,
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
    causation_seed: object | None = None,
) -> None:
    if not tickers or not capabilities:
        qmd_delete_json(f"/computation-targets/{target_id}", timeout=3)
        return
    lineage = causal_identity(
        correlation_seed=target_id,
        causation_seed=causation_seed or target_id,
    )
    parameter_hash = hashlib.sha256(
        json.dumps(
            {
                "capabilities": sorted(set(capabilities)),
                "timeframes": sorted({value.lower() for value in timeframes}),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    qmd_put_json(
        "/computation-targets",
        {
            "target_id": target_id,
            "owner": owner,
            "scope": scope,
            "tickers": tickers,
            "capabilities": capabilities,
            "timeframes": timeframes,
            "parameter_hash": parameter_hash,
            "anchor": "new_york_session",
            "source_revision": "advancing_live",
            "ttl_seconds": max(1, int(ttl_ms) // 1000),
            **lineage,
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
    cutoff = (as_of or datetime.now(UTC)).astimezone(UTC)
    if as_of is None:
        return _live_reference_projection(cutoff)
    source_revision = cutoff.isoformat()
    cached = _REFERENCE_CACHE.get("eligible-market", source_revision=source_revision)
    if cached is not None:
        return cached
    projection = _load_market_reference_projection(cutoff)
    _REFERENCE_CACHE.set(
        "eligible-market",
        projection,
        source_revision=source_revision,
    )
    return projection


def _live_reference_projection(cutoff: datetime) -> dict[str, dict[str, Any]]:
    global _LIVE_REFERENCE_LOADED_AT
    global _LIVE_REFERENCE_PROJECTION
    global _LIVE_REFERENCE_REFRESHING

    with _LIVE_REFERENCE_LOCK:
        projection = _LIVE_REFERENCE_PROJECTION
        loaded_at = _LIVE_REFERENCE_LOADED_AT
        fresh = bool(
            projection is not None
            and loaded_at is not None
            and (cutoff - loaded_at).total_seconds() < REFERENCE_CACHE_SECONDS
        )
        if fresh:
            return projection or {}
        if projection is None:
            projection = _load_market_reference_projection(cutoff)
            _LIVE_REFERENCE_PROJECTION = projection
            _LIVE_REFERENCE_LOADED_AT = cutoff
            return projection
        if not _LIVE_REFERENCE_REFRESHING:
            _LIVE_REFERENCE_REFRESHING = True
            threading.Thread(
                target=_refresh_live_reference_projection,
                args=(cutoff,),
                name="watchlist-reference-refresh",
                daemon=True,
            ).start()
        return projection


def _refresh_live_reference_projection(cutoff: datetime) -> None:
    global _LIVE_REFERENCE_LOADED_AT
    global _LIVE_REFERENCE_PROJECTION
    global _LIVE_REFERENCE_REFRESHING
    global _LIVE_REFERENCE_REFRESH_ERROR

    try:
        projection = _load_market_reference_projection(cutoff)
    except Exception as exc:
        with _LIVE_REFERENCE_LOCK:
            _LIVE_REFERENCE_REFRESH_ERROR = str(exc)
    else:
        with _LIVE_REFERENCE_LOCK:
            _LIVE_REFERENCE_PROJECTION = projection
            _LIVE_REFERENCE_LOADED_AT = cutoff
            _LIVE_REFERENCE_REFRESH_ERROR = ""
    finally:
        with _LIVE_REFERENCE_LOCK:
            _LIVE_REFERENCE_REFRESHING = False


def _load_market_reference_projection(cutoff: datetime) -> dict[str, dict[str, Any]]:
    from src.backend.historical_scanner_service import historical_scanner_reference_projection

    projection = historical_scanner_reference_projection(cutoff)
    source_database = os.environ.get(
        "QMD_HISTORY_CLICKHOUSE_DATABASE", "market_sip_compact"
    ).strip()
    daily_query = daily_market_reference_projection(
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
    daily_rows = client.execute(daily_query)
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
    from src.backend.query_plans.reference_scanner_asof_v1 import (
        QUERY_PLAN_ID as REFERENCE_PLAN_ID,
        QUERY_PLAN_VERSION as REFERENCE_PLAN_VERSION,
    )
    from src.backend.query_plans.sec_fundamentals_asof_v1 import (
        QUERY_PLAN_ID as FUNDAMENTALS_PLAN_ID,
        QUERY_PLAN_VERSION as FUNDAMENTALS_PLAN_VERSION,
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
        value
        for value in compile_data_field_plan(
            discovery, composition_ids=[watchlist_id]
        ).get("technical_timeframes") or []
        if str(value) in {"100ms", "1s", "5s", "10s", "30s", "1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mo", "extended_session", "regular_session"}
    )
    reference = historical_scanner_reference_projection(as_of)
    technical, technical_meta = historical_scanner_technical_projection(
        as_of, calculation_windows=calculation_windows
    )
    fundamentals_requested = any(
        source.startswith("fundamental.") for source in sources
    )
    fundamentals = (
        historical_scanner_fundamental_projection(as_of)
        if fundamentals_requested
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
    candidates = project_data_field_outputs(
        candidates, discovery.get("data_fields") or []
    )
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
        "authority": {
            "scanner": {
                "schema_version": scanner_meta.get("schema_version"),
                "source_revision": scanner_meta.get("source_revision"),
                "snapshot_at_utc": scanner_meta.get("snapshot_at_utc"),
            },
            "technical": {
                "schema_version": technical_meta.get("technical_schema_version"),
                "source_revision": technical_meta.get("source_revision"),
                "windows": technical_meta.get("technical_windows") or {},
            },
            "reference": {
                "query_plan_id": REFERENCE_PLAN_ID,
                "query_plan_version": REFERENCE_PLAN_VERSION,
                "as_of": as_of.astimezone(UTC).isoformat(),
            },
            "fundamentals": (
                {
                    "query_plan_id": FUNDAMENTALS_PLAN_ID,
                    "query_plan_version": FUNDAMENTALS_PLAN_VERSION,
                    "as_of": as_of.astimezone(UTC).isoformat(),
                }
                if fundamentals_requested
                else None
            ),
        },
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
