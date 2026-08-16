from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from src.backend.discovery_projection import (
    configured_discovery_column_ids,
    discovery_runtime_field,
    project_discovery_columns,
)
from src.backend.data_field_contracts import (
    field_instance_ref,
    interval_expression,
    project_composition_data_field_columns,
    project_data_field_outputs,
)
from src.backend.watchlist_runtime_service import normalize_watchlist_candidate
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.watchlist_resolver import evaluate_rule_set_result


NEW_YORK = ZoneInfo("America/New_York")
SIGNAL_STATE_RUN_ID = "market-discovery:signal-stream-state"
SIGNAL_EVENT_RUN_ID = "market-discovery:signal-stream"
SIGNAL_STREAM_SCHEMA_VERSION = 1


class SignalStreamRuntime:
    """Evaluate configured Rule Set edges and retain immutable occurrences."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._states: dict[str, dict[str, dict[str, Any]]] = {}
        self._admissions: dict[str, dict[str, dict[str, Any]]] = {}
        self._hydrated = False

    def resolve(
        self,
        configuration: dict[str, Any],
        candidates: list[dict[str, Any]],
        *,
        as_of: datetime,
        journal: TradingJournal,
    ) -> dict[str, Any]:
        if as_of.tzinfo is None:
            raise ValueError("Signal Stream as_of must be timezone-aware")
        as_of = as_of.astimezone(UTC)
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
        normalized = project_data_field_outputs(project_discovery_columns(
            (normalize_watchlist_candidate(row) for row in candidates),
            column_ids=selected_columns,
        ), discovery.get("data_fields") or [])
        rows_by_ticker = {
            str(row.get("ticker") or row.get("symbol") or "").strip().upper(): row
            for row in normalized
            if str(row.get("ticker") or row.get("symbol") or "").strip()
        }
        stream_snapshots: list[dict[str, Any]] = []
        with self._lock:
            self._hydrate(journal)
            self._prune_admissions(as_of)
            for stream in discovery.get("signal_streams") or []:
                stream_id = str(stream.get("signal_stream_id") or "").strip()
                if not stream_id:
                    continue
                enabled = bool(stream.get("enabled", True))
                selected_rule_ids = [
                    str(value) for value in stream.get("inclusion_rule_sets") or [] if str(value)
                ]
                revision_hash = _definition_revision(stream, rule_sets)
                stream_state = self._states.setdefault(stream_id, {})
                emitted = 0
                matching = 0
                for ticker, row in rows_by_ticker.items():
                    stream_row = project_composition_data_field_columns(
                        [row], stream, discovery.get("column_catalog") or []
                    )[0]
                    matches = enabled and bool(selected_rule_ids) and _matches_stream(
                        stream, selected_rule_ids, rule_sets, stream_row
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
                    should_emit = matches and (
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
                        self._apply_routes(stream, occurrence, as_of)
                        previous["last_emitted_at"] = as_of.isoformat()
                    stream_state[ticker] = {
                        **previous,
                        "matching": matches,
                        "definition_revision": revision_hash,
                        "evaluated_at": as_of.isoformat(),
                    }
                stream_snapshots.append({
                    "signal_stream_id": stream_id,
                    "name": str(stream.get("name") or stream_id),
                    "enabled": enabled,
                    "status": "ready" if enabled and selected_rule_ids else "unconfigured" if enabled else "disabled",
                    "matching_count": matching,
                    "emitted_count": emitted,
                    "definition_revision": revision_hash,
                })
            journal.save_checkpoint(
                SIGNAL_STATE_RUN_ID,
                as_of.isoformat(),
                {"states": self._states, "admissions": self._admissions},
                as_of,
            )
            occurrences = [record.payload for record in journal.signal_stream_records(limit=10_000)]
            return {
                "schema_version": SIGNAL_STREAM_SCHEMA_VERSION,
                "as_of": as_of.isoformat(),
                "status": "ready",
                "signal_streams": stream_snapshots,
                "occurrence_count": len(occurrences),
                "occurrences": occurrences,
                "admissions_by_watchlist": self.admissions_by_watchlist(as_of),
            }

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
        as_of: datetime | None = None,
        limit: int = 5000,
    ) -> dict[str, Any]:
        records = journal.signal_stream_records(
            signal_stream_id=signal_stream_id,
            as_of=as_of,
            limit=limit,
        )
        return {
            "schema_version": SIGNAL_STREAM_SCHEMA_VERSION,
            "as_of": (as_of or datetime.now(UTC)).astimezone(UTC).isoformat(),
            "status": "ready",
            "occurrence_count": len(records),
            "occurrences": [record.payload for record in records],
        }

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
        self._hydrated = True

    def _apply_routes(
        self,
        stream: dict[str, Any],
        occurrence: dict[str, Any],
        as_of: datetime,
    ) -> None:
        ticker = str(occurrence.get("ticker") or "")
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

    def _prune_admissions(self, as_of: datetime) -> None:
        for watchlist_id, rows in list(self._admissions.items()):
            self._admissions[watchlist_id] = {
                ticker: row
                for ticker, row in rows.items()
                if _parse_datetime(row.get("expires_at")) is None
                or _parse_datetime(row.get("expires_at")) > as_of
            }


def _matches_stream(
    stream: dict[str, Any],
    selected_rule_ids: list[str],
    rule_sets: dict[str, dict[str, Any]],
    row: dict[str, Any],
) -> bool:
    results = [evaluate_rule_set_result(rule_sets.get(rule_id), row) for rule_id in selected_rule_ids]
    return any(results) if str(stream.get("inclusion_operator") or "all") == "any" else all(results)


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
            instance_ref = field_instance_ref(field_ref, interval)
            field_evidence[instance_ref] = {
                "field_ref": field_ref,
                "interval": interval,
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


SIGNAL_STREAM_RUNTIME = SignalStreamRuntime()
