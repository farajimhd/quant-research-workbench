from __future__ import annotations

import hashlib
import json
import threading
import time as monotonic_time
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from src.backend.discovery_projection import (
    configured_discovery_column_ids,
    discovery_runtime_field,
    project_discovery_columns,
)
from src.backend.data_field_contracts import (
    compile_data_field_plan,
    field_instance_ref,
    interval_expression,
    project_composition_data_field_columns,
    project_data_field_outputs,
)
from src.backend.watchlist_runtime_service import (
    focused_target_contract,
    normalize_watchlist_candidate,
    publish_computation_target,
)
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.watchlist_resolver import evaluate_rule_sets_frame


NEW_YORK = ZoneInfo("America/New_York")
SIGNAL_STATE_RUN_ID = "market-discovery:signal-stream-state"
SIGNAL_EVENT_RUN_ID = "market-discovery:signal-stream"
SIGNAL_STREAM_SCHEMA_VERSION = 1
SIGNAL_STREAM_SNAPSHOT_CACHE_SECONDS = 1.0
DEFAULT_CORE_COMPUTATION_CANDIDATE_LIMIT = 512
MAX_CORE_COMPUTATION_CANDIDATE_LIMIT = 2_000


class SignalStreamRuntime:
    """Evaluate configured Rule Set edges and retain immutable occurrences."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshot_lock = threading.RLock()
        self._states: dict[str, dict[str, dict[str, Any]]] = {}
        self._admissions: dict[str, dict[str, dict[str, Any]]] = {}
        self._diagnostics: dict[str, dict[str, Any]] = {}
        self._published_targets: set[str] = set()
        self._session_key = ""
        self._hydrated = False
        self._snapshot_cache: dict[tuple[str, str, int, str], tuple[float, dict[str, Any]]] = {}
        self._live_occurrences: tuple[str, list[dict[str, Any]]] | None = None

    def seed_computation_targets(
        self,
        configuration: dict[str, Any],
        candidates: list[dict[str, Any]],
        *,
        watchlist_runtime: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Lease trigger operands and frozen evidence for each enabled stream."""

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
        core_candidates = bounded_core_computation_candidates(candidates)
        watchlist_members = {
            str(snapshot.get("watchlist_id") or ""): sorted({
                str(member.get("ticker") or member.get("symbol") or "").strip().upper()
                for member in snapshot.get("members") or []
                if str(member.get("ticker") or member.get("symbol") or "").strip()
            })
            for snapshot in dict(watchlist_runtime or {}).get("watchlists") or []
        }
        seeds: list[dict[str, Any]] = []
        active_target_ids: set[str] = set()
        with self._lock:
            previously_published = set(self._published_targets)
        for stream in discovery.get("signal_streams") or []:
            stream_id = str(stream.get("signal_stream_id") or "").strip()
            if not stream_id or not bool(stream.get("enabled", True)):
                continue
            if str(stream.get("source_type") or "core_scan") == "news_events":
                continue
            capabilities, timeframes = focused_target_contract(
                stream,
                rule_sets,
                calculations,
                column_sources,
                discovery.get("data_fields") or [],
            )
            if not capabilities:
                continue
            source_type = str(stream.get("source_type") or "core_scan")
            source_id = str(stream.get("source_id") or stream.get("source_scan_id") or "")
            source_candidate_count = len(core_candidates)
            candidate_limit = None
            if source_type == "core_scan":
                candidate_limit = max(
                    1,
                    min(
                        int(
                            stream.get("computation_candidate_limit")
                            or DEFAULT_CORE_COMPUTATION_CANDIDATE_LIMIT
                        ),
                        MAX_CORE_COMPUTATION_CANDIDATE_LIMIT,
                    ),
                )
                source_candidate_count = len({
                    str(row.get("ticker") or row.get("symbol") or "").strip().upper()
                    for row in candidates
                    if str(row.get("ticker") or row.get("symbol") or "").strip()
                })
                tickers = [row[1] for row in core_candidates[:candidate_limit]]
            else:
                tickers = watchlist_members.get(source_id, [])
                source_candidate_count = len(tickers)
            target_id = f"signal-stream:{stream_id}"
            active_target_ids.add(target_id)
            if tickers or target_id in previously_published:
                publish_computation_target(
                    target_id,
                    tickers,
                    capabilities,
                    timeframes,
                    owner="backend.market_discovery",
                    scope="signal_stream",
                    ttl_ms=max(60_000, int(stream.get("refresh_interval_ms") or 1_000) * 5),
                    causation_seed=f"{stream_id}:{source_type}:{source_id}",
                )
            if tickers:
                seeds.append({
                    "signal_stream_id": stream_id,
                    "source_type": source_type,
                    "source_id": source_id,
                    "candidate_count": len(tickers),
                    "source_candidate_count": source_candidate_count,
                    "computation_candidate_limit": candidate_limit,
                    "degraded": source_candidate_count > len(tickers),
                    "degradation_reason": (
                        "bounded_core_computation_admission"
                        if source_candidate_count > len(tickers)
                        else ""
                    ),
                    "capabilities": capabilities,
                    "timeframes": timeframes,
                })
        for target_id in previously_published - active_target_ids:
            publish_computation_target(
                target_id,
                [],
                [],
                [],
                owner="backend.market_discovery",
                scope="signal_stream",
                ttl_ms=5_000,
            )
        with self._lock:
            self._published_targets = {
                f"signal-stream:{row['signal_stream_id']}" for row in seeds
            }
        return seeds

    def resolve(
        self,
        configuration: dict[str, Any],
        candidates: list[dict[str, Any]],
        *,
        as_of: datetime,
        journal: TradingJournal,
        watchlist_runtime: dict[str, Any] | None = None,
        include_occurrences: bool = True,
        data_fields_projected: bool = False,
    ) -> dict[str, Any]:
        if as_of.tzinfo is None:
            raise ValueError("Signal Stream as_of must be timezone-aware")
        as_of = as_of.astimezone(UTC)
        session = signal_stream_session(as_of)
        discovery = dict(configuration.get("market_discovery") or {})
        rule_sets = {
            str(row.get("rule_set_id") or ""): row
            for row in discovery.get("rule_sets") or []
        }
        columns = {
            str(row.get("column_id") or ""): row
            for row in discovery.get("column_catalog") or []
        }
        selected_columns = configured_discovery_column_ids(configuration)
        data_field_plan = compile_data_field_plan(discovery)
        active_field_refs = list(data_field_plan.get("field_refs") or [])
        normalized = project_discovery_columns(
            (normalize_watchlist_candidate(row) for row in candidates),
            column_ids=selected_columns,
        )
        if not data_fields_projected:
            normalized = project_data_field_outputs(
                normalized,
                discovery.get("data_fields") or [],
                field_refs=active_field_refs,
                field_instances=list(data_field_plan.get("field_instances") or []),
            )
        rows_by_ticker = {
            str(row.get("ticker") or row.get("symbol") or "").strip().upper(): row
            for row in normalized
            if str(row.get("ticker") or row.get("symbol") or "").strip()
        }
        watchlist_members = {
            str(snapshot.get("watchlist_id") or ""): {
                str(member.get("ticker") or member.get("symbol") or "").strip().upper()
                for member in snapshot.get("members") or []
                if str(member.get("ticker") or member.get("symbol") or "").strip()
            }
            for snapshot in dict(watchlist_runtime or {}).get("watchlists") or []
            if str(snapshot.get("watchlist_id") or "")
        }
        stream_snapshots: list[dict[str, Any]] = []
        new_occurrences: list[dict[str, Any]] = []
        with self._lock:
            self._hydrate(journal)
            dirty = False
            if self._session_key != session["session_key"] or (
                not session["active"] and (self._states or self._admissions)
            ):
                self._states = {}
                self._admissions = {}
                self._session_key = session["session_key"]
                dirty = True
            dirty = self._prune_admissions(as_of) or dirty
            active_stream_ids: set[str] = set()
            for stream in discovery.get("signal_streams") or []:
                stream_id = str(stream.get("signal_stream_id") or "").strip()
                if not stream_id:
                    continue
                active_stream_ids.add(stream_id)
                enabled = bool(stream.get("enabled", True))
                source_type = str(stream.get("source_type") or "core_scan")
                if source_type == "news_events":
                    prior = dict(self._diagnostics.get(stream_id) or {})
                    stream_snapshots.append({
                        **prior,
                        "signal_stream_id": stream_id,
                        "name": str(stream.get("name") or stream_id),
                        "enabled": enabled,
                        "status": str(prior.get("status") or ("ready" if enabled else "disabled")),
                        "source_type": source_type,
                        "source_id": str(stream.get("source_id") or ""),
                    })
                    continue
                source_id = str(
                    stream.get("source_id")
                    or stream.get("source_scan_id")
                    or discovery.get("core_scan", {}).get("scan_id")
                    or ""
                )
                source_ready = source_type == "core_scan" or source_id in watchlist_members
                source_tickers = (
                    set(rows_by_ticker)
                    if source_type == "core_scan"
                    else watchlist_members.get(source_id, set())
                )
                selected_rule_ids = [
                    str(value) for value in stream.get("inclusion_rule_sets") or [] if str(value)
                ]
                revision_hash = _definition_revision(stream, rule_sets)
                occurrence_source = str(stream.get("occurrence_source") or "rule_evaluator")
                stream_state = self._states.setdefault(stream_id, {})
                emitted = 0
                matching = 0
                stream_rows = project_composition_data_field_columns(
                    [rows_by_ticker[ticker] for ticker in source_tickers if ticker in rows_by_ticker],
                    stream,
                    discovery.get("column_catalog") or [],
                )
                masks = evaluate_rule_sets_frame(
                    (rule_sets[rule_id] for rule_id in selected_rule_ids if rule_id in rule_sets),
                    stream_rows,
                )
                current_tickers: set[str] = set()
                for index, stream_row in enumerate(stream_rows):
                    ticker = str(stream_row.get("ticker") or stream_row.get("symbol") or "").strip().upper()
                    if not ticker:
                        continue
                    current_tickers.add(ticker)
                    results = [bool((masks.get(rule_id) or [False] * len(stream_rows))[index]) for rule_id in selected_rule_ids]
                    matches = session["active"] and enabled and source_ready and bool(results) and (
                        any(results) if str(stream.get("inclusion_operator") or "all") == "any" else all(results)
                    )
                    matching += int(matches)
                    previous = stream_state.get(ticker, {})
                    previous_match = bool(previous.get("matching")) and str(
                        previous.get("definition_revision") or ""
                    ) == revision_hash
                    last_emitted = _parse_datetime(previous.get("last_emitted_at"))
                    rearm_policy = str(stream.get("rearm_policy") or "after_false")
                    cooldown_ms = max(0, int(stream.get("cooldown_ms") or 0))
                    cooldown_elapsed = (
                        last_emitted is None
                        or as_of >= last_emitted + timedelta(milliseconds=cooldown_ms)
                    )
                    should_emit = occurrence_source == "rule_evaluator" and matches and (
                        not previous_match
                        or (rearm_policy == "after_cooldown" and cooldown_elapsed)
                    )
                    if should_emit:
                        occurrence = _occurrence(
                            stream,
                            stream_row,
                            columns,
                            as_of=as_of,
                            definition_revision=revision_hash,
                        )
                        _, inserted = journal.append_once(
                            run_id=SIGNAL_EVENT_RUN_ID,
                            category="market_discovery_signal",
                            entity_type="signal_occurrence",
                            entity_id=str(occurrence["event_id"]),
                            event_time=as_of,
                            payload=occurrence,
                        )
                        emitted += int(inserted)
                        if inserted:
                            with self._snapshot_lock:
                                self._snapshot_cache.clear()
                            new_occurrences.append(occurrence)
                        dirty = self._apply_routes(stream, occurrence, as_of) or dirty
                        previous["last_emitted_at"] = as_of.isoformat()
                    next_state = {
                        **previous,
                        "matching": matches,
                        "definition_revision": revision_hash,
                    }
                    if stream_state.get(ticker) != next_state:
                        stream_state[ticker] = next_state
                        dirty = True
                for missing_ticker in set(stream_state) - current_tickers:
                    previous = stream_state[missing_ticker]
                    if bool(previous.get("matching")) or str(previous.get("definition_revision") or "") != revision_hash:
                        stream_state[missing_ticker] = {
                            **previous,
                            "matching": False,
                            "definition_revision": revision_hash,
                        }
                        dirty = True
                stream_snapshots.append({
                    "signal_stream_id": stream_id,
                    "name": str(stream.get("name") or stream_id),
                    "enabled": enabled,
                    "status": "session_closed" if enabled and not session["active"] else "ready" if enabled and source_ready and selected_rule_ids else "source_unavailable" if enabled and not source_ready else "unconfigured" if enabled else "disabled",
                    "source_type": source_type,
                    "source_id": source_id,
                    "occurrence_source": occurrence_source,
                    "candidate_count": len(stream_rows),
                    "matching_count": matching,
                    "emitted_count": emitted,
                    "definition_revision": revision_hash,
                })
            for removed_stream_id in set(self._states) - active_stream_ids:
                del self._states[removed_stream_id]
                dirty = True
            self._diagnostics = {
                str(row.get("signal_stream_id") or ""): dict(row)
                for row in stream_snapshots
                if str(row.get("signal_stream_id") or "")
            }
            if dirty:
                journal.save_checkpoint(
                    SIGNAL_STATE_RUN_ID,
                    as_of.isoformat(),
                    {"states": self._states, "admissions": self._admissions, "session_key": self._session_key},
                    as_of,
                )
            occurrences = []
            if include_occurrences and session["active"]:
                occurrences = [
                    record.payload
                    for record in journal.signal_stream_records(
                        from_time=session["start_at"],
                        as_of=as_of,
                        limit=10_000,
                    )
                ]
                # The live evaluator already paid the durable journal read.
                # Publish that immutable session view for Canvas snapshots so
                # UI polling never queues behind the next writer transaction.
                with self._snapshot_lock:
                    self._live_occurrences = (
                        str(session["session_key"]),
                        [dict(row) for row in occurrences],
                    )
                    self._snapshot_cache.clear()
            return {
                "schema_version": SIGNAL_STREAM_SCHEMA_VERSION,
                "as_of": as_of.isoformat(),
                "status": "ready",
                "session": _session_payload(session),
                "signal_streams": stream_snapshots,
                "occurrence_count": len(occurrences) if include_occurrences else sum(int(row.get("emitted_count") or 0) for row in stream_snapshots),
                "occurrences": occurrences,
                "new_occurrences": new_occurrences,
                "admissions_by_watchlist": self.admissions_by_watchlist(as_of),
            }

    def append_external_event_rows(
        self,
        configuration: dict[str, Any],
        *,
        signal_stream_id: str,
        rows: list[dict[str, Any]],
        journal: TradingJournal,
        event_run_id: str = SIGNAL_EVENT_RUN_ID,
        include_existing: bool = False,
    ) -> list[dict[str, Any]]:
        """Freeze externally published event rows under a configured stream."""

        discovery = dict(configuration.get("market_discovery") or {})
        stream = next(
            (
                dict(value)
                for value in discovery.get("signal_streams") or []
                if str(value.get("signal_stream_id") or "") == signal_stream_id
            ),
            None,
        )
        if stream is None or not bool(stream.get("enabled", True)):
            return []
        rule_sets = {
            str(value.get("rule_set_id") or ""): dict(value)
            for value in discovery.get("rule_sets") or []
        }
        columns = {
            str(value.get("column_id") or ""): dict(value)
            for value in discovery.get("column_catalog") or []
        }
        revision = _definition_revision(stream, rule_sets)
        inserted_rows: list[dict[str, Any]] = []
        new_count = 0
        projected_rows = project_discovery_columns(
            rows,
            column_ids=(str(value) for value in stream.get("columns") or []),
        )
        for row in projected_rows:
            available_at = _parse_datetime(row.get("available_at"))
            if available_at is None:
                continue
            occurrence = _occurrence(
                stream,
                row,
                columns,
                as_of=available_at,
                definition_revision=revision,
            )
            source_event_id = str(row.get("source_event_id") or "")
            if source_event_id:
                occurrence["event_id"] = hashlib.sha256(
                    f"{signal_stream_id}|{revision}|{occurrence['ticker']}|{source_event_id}".encode("utf-8")
                ).hexdigest()
                occurrence["signal_id"] = occurrence["event_id"]
            record, inserted = journal.append_once(
                run_id=event_run_id,
                category="market_discovery_signal",
                entity_type="signal_occurrence",
                entity_id=str(occurrence["event_id"]),
                event_time=available_at,
                payload=occurrence,
            )
            if inserted or include_existing:
                inserted_rows.append(dict(record.payload))
            if inserted:
                new_count += 1
        with self._lock:
            if new_count:
                with self._snapshot_lock:
                    self._snapshot_cache.clear()
            self._diagnostics[signal_stream_id] = {
                "signal_stream_id": signal_stream_id,
                "name": str(stream.get("name") or signal_stream_id),
                "enabled": True,
                "status": "ready",
                "source_type": str(stream.get("source_type") or "external_events"),
                "source_id": str(stream.get("source_id") or ""),
                "candidate_count": len(rows),
                "matching_count": len(rows),
                "emitted_count": new_count,
                "definition_revision": revision,
            }
        return inserted_rows

    def admissions_by_watchlist(self, as_of: datetime) -> dict[str, list[dict[str, Any]]]:
        self._prune_admissions(as_of.astimezone(UTC))
        return {
            watchlist_id: list(sorted(rows.values(), key=lambda row: str(row.get("event_time") or ""), reverse=True))
            for watchlist_id, rows in self._admissions.items()
            if rows
        }

    def snapshot(
        self,
        journal: TradingJournal,
        *,
        signal_stream_id: str = "",
        run_id: str = "",
        as_of: datetime | None = None,
        limit: int = 5000,
        configuration: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cutoff = (as_of or datetime.now(UTC)).astimezone(UTC)
        session = signal_stream_session(cutoff)
        configuration_streams = [
            dict(stream)
            for stream in dict(dict(configuration or {}).get("market_discovery") or {}).get("signal_streams") or []
            if not signal_stream_id or str(stream.get("signal_stream_id") or "") == signal_stream_id
        ]
        configuration_key = hashlib.sha256(
            json.dumps(configuration_streams, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        cache_key = (
            signal_stream_id,
            str(session["session_key"]),
            max(1, min(int(limit), 50_000)),
            configuration_key,
        )
        now = monotonic_time.monotonic()
        if as_of is None:
            with self._snapshot_lock:
                cached = self._snapshot_cache.get(cache_key)
                if cached is not None and cached[0] > now:
                    return cached[1]

        # Occurrence history is independently durable. Never queue a Canvas
        # read behind the full-universe transition evaluation protected by the
        # runtime state lock; diagnostics may be one cycle stale while resolve
        # is active, but immutable occurrences remain authoritative.
        cached_occurrences: list[dict[str, Any]] | None = None
        if as_of is None and session["active"]:
            with self._snapshot_lock:
                if self._live_occurrences and self._live_occurrences[0] == str(session["session_key"]):
                    cached_occurrences = [
                        dict(row)
                        for row in self._live_occurrences[1]
                        if not signal_stream_id or str(row.get("signal_stream_id") or "") == signal_stream_id
                    ][:limit]
        if cached_occurrences is None:
            records = journal.signal_stream_records(
                run_id=run_id,
                signal_stream_id=signal_stream_id,
                from_time=session["start_at"] if session["active"] else cutoff,
                as_of=cutoff,
                limit=limit,
            ) if session["active"] else []
            occurrences = [record.payload for record in records]
        else:
            occurrences = cached_occurrences
        diagnostics: dict[str, dict[str, Any]] = {}
        if self._lock.acquire(blocking=False):
            try:
                diagnostics = {key: dict(value) for key, value in self._diagnostics.items()}
            finally:
                self._lock.release()
        definitions = [
            {
                **diagnostics.get(str(stream.get("signal_stream_id") or ""), {}),
                "signal_stream_id": str(stream.get("signal_stream_id") or ""),
                "name": str(stream.get("name") or stream.get("signal_stream_id") or "Signal Stream"),
                "enabled": bool(stream.get("enabled", True)),
                "source_type": str(stream.get("source_type") or "core_scan"),
                "source_id": str(stream.get("source_id") or stream.get("source_scan_id") or ""),
                "configured": bool(stream.get("inclusion_rule_sets")),
            }
            for stream in configuration_streams
        ]
        payload = {
            "schema_version": SIGNAL_STREAM_SCHEMA_VERSION,
            "as_of": cutoff.isoformat(),
            "status": "ready",
            "session": _session_payload(session),
            "signal_streams": definitions,
            "occurrence_count": len(occurrences),
            "occurrences": occurrences,
        }
        if as_of is None:
            with self._snapshot_lock:
                if len(self._snapshot_cache) >= 32:
                    self._snapshot_cache.clear()
                self._snapshot_cache[cache_key] = (
                    monotonic_time.monotonic() + SIGNAL_STREAM_SNAPSHOT_CACHE_SECONDS,
                    payload,
                )
        return payload

    def _hydrate(self, journal: TradingJournal) -> None:
        if self._hydrated:
            return
        checkpoint = journal.load_checkpoint(SIGNAL_STATE_RUN_ID)
        if checkpoint:
            state = dict(checkpoint.get("state") or {})
            self._states = {
                str(stream_id): {str(ticker): dict(value) for ticker, value in dict(rows).items()}
                for stream_id, rows in dict(state.get("states") or {}).items()
            }
            self._admissions = {
                str(watchlist_id): {str(ticker): dict(value) for ticker, value in dict(rows).items()}
                for watchlist_id, rows in dict(state.get("admissions") or {}).items()
            }
            self._session_key = str(state.get("session_key") or "")
        self._hydrated = True

    def _apply_routes(
        self,
        stream: dict[str, Any],
        occurrence: dict[str, Any],
        as_of: datetime,
    ) -> bool:
        ticker = str(occurrence.get("ticker") or "")
        changed = False
        for route in stream.get("watchlist_routes") or []:
            watchlist_id = str(route.get("watchlist_id") or "")
            if not watchlist_id:
                continue
            self._admissions.setdefault(watchlist_id, {})[ticker] = {
                **dict(occurrence.get("evidence") or {}),
                "ticker": ticker,
                "membership_reason": f"signal stream {stream.get('name') or stream.get('signal_stream_id')}",
                "causation_signal_event_id": occurrence.get("event_id"),
                "causation_signal_stream_id": occurrence.get("signal_stream_id"),
                "event_time": occurrence.get("event_time"),
                "expires_at": _route_expiry(route, as_of),
            }
            changed = True
        return changed

    def _prune_admissions(self, as_of: datetime) -> bool:
        changed = False
        for watchlist_id, rows in list(self._admissions.items()):
            retained = {
                ticker: row
                for ticker, row in rows.items()
                if _parse_datetime(row.get("expires_at")) is None
                or _parse_datetime(row.get("expires_at")) > as_of
            }
            if retained != rows:
                self._admissions[watchlist_id] = retained
                changed = True
        return changed


def bounded_core_computation_candidates(
    candidates: list[dict[str, Any]],
) -> list[tuple[tuple[float, float, str], str]]:
    """Rank a cheap Core Scan projection before leasing expensive QMD state."""
    ranked: dict[str, tuple[float, float, str]] = {}
    for row in candidates:
        ticker = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
        if not ticker:
            continue
        liquidity_rank = _finite_rank(
            row.get("liquidity_rank", row.get("market.liquidity_rank"))
        )
        activity_rank = _finite_rank(
            row.get("activity_rank", row.get("market.activity_rank"))
        )
        rank = (liquidity_rank, activity_rank, ticker)
        current = ranked.get(ticker)
        if current is None or rank < current:
            ranked[ticker] = rank
    return sorted((rank, ticker) for ticker, rank in ranked.items())


def _finite_rank(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float("inf")
    return parsed if parsed == parsed and parsed != float("inf") else float("inf")


def _definition_revision(
    stream: dict[str, Any], rule_sets: dict[str, dict[str, Any]]
) -> str:
    selected_rule_ids = [
        str(value) for value in stream.get("inclusion_rule_sets") or [] if str(value)
    ]
    payload = {
        key: stream.get(key)
        for key in (
            "signal_stream_id", "revision", "inclusion_rule_sets", "inclusion_operator",
            "source_type", "source_id",
            "occurrence_source",
            "columns", "column_intervals", "trigger_policy", "rearm_policy", "cooldown_ms", "watchlist_routes",
        )
    }
    payload["rule_sets"] = [rule_sets.get(rule_id, {}) for rule_id in selected_rule_ids]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _occurrence(
    stream: dict[str, Any],
    row: dict[str, Any],
    columns: dict[str, dict[str, Any]],
    *,
    as_of: datetime,
    definition_revision: str,
) -> dict[str, Any]:
    ticker = str(row.get("ticker") or row.get("symbol") or "").upper()
    event_id = hashlib.sha256(
        f"{stream.get('signal_stream_id')}|{definition_revision}|{ticker}|{as_of.isoformat()}".encode("utf-8")
    ).hexdigest()
    evidence: dict[str, Any] = {}
    field_evidence: dict[str, Any] = {}
    null_reasons: dict[str, str] = {}
    for column_id in stream.get("columns") or []:
        column_id = str(column_id)
        column = columns.get(column_id, {})
        runtime_field = discovery_runtime_field(str(column.get("source_id") or column_id))
        value = row.get(column_id, row.get(runtime_field))
        evidence[column_id] = value
        field_ref = str(column.get("field_ref") or "")
        if field_ref:
            interval = interval_expression(dict(stream.get("column_intervals") or {}).get(column_id))
            aggregation = dict(stream.get("column_aggregations") or {}).get(column_id)
            instance_ref = field_instance_ref(field_ref, interval, aggregation)
            field_evidence[instance_ref] = {
                "field_ref": field_ref,
                "interval": interval,
                "aggregation": aggregation or "",
                "value": row.get(instance_ref) if row.get(instance_ref) is not None else value,
                "available_at": row.get(f"{instance_ref}__available_at"),
                "null_reason": row.get(f"{instance_ref}__null_reason"),
            }
        if value in (None, ""):
            null_reasons[column_id] = str(
                row.get(f"{column_id}_null_reason")
                or row.get(f"{runtime_field}_null_reason")
                or "not_available_at_trigger"
            )
    return {
        "schema_version": SIGNAL_STREAM_SCHEMA_VERSION,
        "event_id": event_id,
        "signal_id": event_id,
        "signal_stream_id": str(stream.get("signal_stream_id") or ""),
        "signal_stream_name": str(stream.get("name") or stream.get("signal_stream_id") or "Signal Stream"),
        "definition_revision": definition_revision,
        "configured_revision": int(stream.get("revision") or 1),
        "ticker": ticker,
        "company_name": str(row.get("company_name") or row.get("issuer_name") or ""),
        "issuer_name": str(row.get("issuer_name") or row.get("company_name") or ""),
        "country": str(
            row.get("country")
            or row.get("company_country_code")
            or row.get("domicile_country_code")
            or ""
        ),
        "logo_url": str(row.get("logo_url") or ""),
        "live_news_recency": str(row.get("live_news_recency") or "none"),
        "live_news_count": int(row.get("live_news_count") or 0),
        "today_news_count": int(row.get("today_news_count") or 0),
        **{
            key: row.get(key)
            for key in (
                "latest_news_id", "latest_news_title",
                "news_synthesis", "news_synthesis_class", "news_synthesis_purpose",
                "news_synthesis_origin", "news_synthesis_direction",
                "news_synthesis_event", "news_synthesis_text",
                "news_ai_review", "news_ai_review_state", "news_ai_eligibility",
                "news_ai_sentiment", "news_ai_positive_probability",
                "news_ai_negative_probability", "news_deepfm_probability",
                "news_deepfm_eligibility", "news_ai_reaction",
                "news_ai_reaction_state", "news_ai_reaction_confidence",
                "news_ai_reaction_up_probability", "news_ai_reaction_down_probability",
                "news_ai_reaction_regime",
            )
            if row.get(key) not in (None, "")
        },
        "sec_recency": str(row.get("sec_recency") or "none"),
        "sec_count": int(row.get("sec_count") or 0),
        "sec_labels": str(row.get("sec_labels") or ""),
        "sec_synthesis_count": int(row.get("sec_synthesis_count") or 0),
        "sec_synthesis_direction": str(row.get("sec_synthesis_direction") or ""),
        "sec_review_status": str(row.get("sec_review_status") or ""),
        "sec_review_fundamental_direction": str(row.get("sec_review_fundamental_direction") or ""),
        "conid": int(row.get("ibkr_conid") or row.get("conid") or 0),
        "event_time": as_of.isoformat(),
        "effective_at": as_of.isoformat(),
        "available_at": as_of.isoformat(),
        "signal_state": "triggered",
        "trigger_policy": str(stream.get("trigger_policy") or "false_to_true"),
        "matched_rule_set_ids": [str(value) for value in stream.get("inclusion_rule_sets") or []],
        "evidence": evidence,
        "field_evidence": field_evidence,
        "evidence_null_reasons": null_reasons,
        **evidence,
    }


def _route_expiry(route: dict[str, Any], as_of: datetime) -> str | None:
    policy = str(route.get("membership_expiry") or "end_of_trading_day")
    if policy == "never":
        return None
    if policy == "time_to_live":
        return (as_of + timedelta(milliseconds=max(1, int(route.get("membership_ttl_ms") or 0)))).isoformat()
    local = as_of.astimezone(NEW_YORK)
    expiry = datetime.combine(local.date(), time(20, 0), NEW_YORK)
    if expiry <= local:
        expiry += timedelta(days=1)
    return expiry.astimezone(UTC).isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def signal_stream_session(as_of: datetime) -> dict[str, Any]:
    """Return the exchange-day Signal Stream presentation and state window."""

    cutoff = as_of.astimezone(UTC)
    local = cutoff.astimezone(NEW_YORK)
    start_local = datetime.combine(local.date(), time(4, 0), NEW_YORK)
    end_local = datetime.combine(local.date(), time(20, 0), NEW_YORK)
    is_trading_day = local.weekday() < 5
    active = is_trading_day and start_local <= local < end_local
    return {
        "session_key": local.date().isoformat(),
        "active": active,
        "is_trading_day": is_trading_day,
        "start_at": start_local.astimezone(UTC),
        "end_at": end_local.astimezone(UTC),
    }


def _session_payload(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_date": str(session["session_key"]),
        "active": bool(session["active"]),
        "is_trading_day": bool(session["is_trading_day"]),
        "start_at": session["start_at"].isoformat(),
        "end_at": session["end_at"].isoformat(),
        "timezone": "America/New_York",
        "retention": "premarket_to_after_hours",
    }


SIGNAL_STREAM_RUNTIME = SignalStreamRuntime()
