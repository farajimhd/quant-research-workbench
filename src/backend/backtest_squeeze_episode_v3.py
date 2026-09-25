"""Inactive, closed V3 seal for fixed completed-bar squeeze occurrences.

This module does not publish or accept generic Signal Stream occurrences. A
whole-run V3 chain reader is required before these rows can serve the UI.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re
from typing import Any, Mapping, Sequence
from uuid import UUID

from src.backend.backtest_squeeze_episode_projection import project_fixed_squeeze_episode
from src.backend.backtest_squeeze_episode_schema import (
    RESERVATION_REASON, SQUEEZE_COMMIT_V3, SQUEEZE_EPISODE,
)
from src.backend.backtest_reservation_reason_v3 import seal_reservation_reason_family_v3
from src.backend.fixed_bar_signal import CONTRACT, STREAM_ID
from src.trading_runtime.arte_journal_schema import (
    storage_preflight, versioned_journal_v2_contracts,
)
from src.trading_runtime.arte_journal_writer import _literal, _rows, _verify_recovery_chunk
from src.trading_runtime.journal_contract import JournalRecord, canonical_json


_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ROW_COLUMNS = {name for name, _ in SQUEEZE_EPISODE.columns}
_COMMIT_COLUMNS = {name for name, _ in SQUEEZE_COMMIT_V3.columns}


@dataclass(frozen=True, slots=True)
class V3CommittedPrefix:
    run_id: str
    last_sequence: int
    last_batch_id: str
    source_cursor: str
    status: str
    batch_ids: tuple[str, ...]
    occurrences: tuple[dict[str, Any], ...]


def _canonical_row(row: Mapping[str, Any], *, stored_utc: bool = False) -> dict[str, Any]:
    if set(row) != _ROW_COLUMNS - {"content_hash"}:
        raise ValueError("Squeeze row columns differ from V3 contract")
    result: dict[str, Any] = {}
    for name, kind in SQUEEZE_EPISODE.columns:
        if name == "content_hash":
            continue
        value = row[name]
        if kind == "UUID":
            result[name] = str(UUID(str(value)))
        elif kind == "FixedString(64)":
            if not isinstance(value, str) or not _HEX.fullmatch(value):
                raise ValueError(f"Invalid squeeze {name}")
            result[name] = value
        elif kind.startswith("DateTime64"):
            source = str(value)
            if stored_utc:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6}", source) is None:
                    raise ValueError(f"Invalid stored squeeze {name}")
                source += "+00:00"
            at = datetime.fromisoformat(source.replace("Z", "+00:00"))
            if at.tzinfo is None:
                raise ValueError(f"Invalid squeeze {name}")
            result[name] = at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
        elif kind.startswith("Decimal"):
            try:
                number = Decimal(str(value))
                scaled = number.quantize(Decimal("0.000000000000000001"))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"Invalid squeeze {name}") from exc
            if not number.is_finite() or number != scaled or abs(number) >= Decimal(10) ** 20:
                raise ValueError(f"Invalid squeeze {name}")
            result[name] = format(scaled, "f")
        elif kind == "UInt64":
            if type(value) is not int or not 0 <= value < 2**64:
                raise ValueError(f"Invalid squeeze {name}")
            result[name] = value
        elif kind == "Date":
            result[name] = str(value)
            datetime.fromisoformat(result[name])
        else:
            if not isinstance(value, str):
                raise ValueError(f"Invalid squeeze {name}")
            result[name] = value
    return result


def _digest(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def project_squeeze_row_v3(
    record: JournalRecord, *, batch_id: str, expected_market_plan_token: str,
    expected_query_sha256: str,
) -> dict[str, Any]:
    """Project only a pinned source-native start into its immutable batch."""
    row = project_fixed_squeeze_episode(
        record, expected_market_plan_token=expected_market_plan_token,
        expected_query_sha256=expected_query_sha256)
    row["batch_id"] = str(UUID(batch_id))
    row["content_hash"] = _digest(_canonical_row(row))
    return row


def project_squeeze_batch_v3(
    record: JournalRecord, *, run_month: date, attempt_id: str,
    batch_id: str, prior_batch_id: str, source_cursor: str,
    expected_market_plan_token: str, expected_query_sha256: str,
) -> Any:
    """Project one closed occurrence with its ordinary normalized event parent."""
    from src.trading_runtime.arte_journal_writer import (
        TypedJournalBatch, V3SqueezeBatch, typed_row,
    )

    child = project_squeeze_row_v3(
        record, batch_id=batch_id,
        expected_market_plan_token=expected_market_plan_token,
        expected_query_sha256=expected_query_sha256)
    if (record.event_time.tzinfo is None or record.recorded_at.tzinfo is None
            or child["event_month"] != run_month.isoformat()):
        raise ValueError("V3 occurrence lies outside its pinned run month")
    event = typed_row("trading_event_v1", {
        "run_id": record.run_id, "event_month": child["event_month"],
        "attempt_id": attempt_id, "batch_id": batch_id,
        "record_id": record.record_id, "sequence": record.sequence,
        "event_time": record.event_time.astimezone(timezone.utc).isoformat(),
        "recorded_at": record.recorded_at.astimezone(timezone.utc).isoformat(),
        "category": record.category, "entity_type": record.entity_type,
        "entity_id": record.entity_id, "account_id": record.account_id,
        "correlation_id": str(record.payload.get("correlation_id") or ""),
        "causation_id": str(record.payload.get("causation_id") or ""),
    })
    return V3SqueezeBatch(TypedJournalBatch(
        record.run_id, run_month, attempt_id, batch_id, prior_batch_id,
        record.sequence, record.sequence, source_cursor, "running", (event,)),
        (child,))


def coalesce_squeeze_units_v3(units: tuple[Any, ...]) -> Any:
    """Rekey a bounded contiguous V3 microbatch, including every child hash."""
    from src.trading_runtime.arte_journal_writer import (
        V3SqueezeBatch, _coalesce_unpublished,
    )

    if not units or any(type(unit) is not V3SqueezeBatch for unit in units):
        raise ValueError("V3 coalescing requires closed typed units")
    base = _coalesce_unpublished(tuple(unit.base for unit in units))
    episodes = []
    reservation_reasons = []
    pinned = set()
    for unit in units:
        for row in unit.episodes:
            pinned.add((row["market_plan_token"], row["query_sha256"],
                        row["source_authority"]))
            values = {key: value for key, value in row.items()
                      if key != "content_hash"}
            values["batch_id"] = base.batch_id
            values["content_hash"] = _digest(_canonical_row(values))
            episodes.append(values)
        for row in unit.reservation_reasons:
            values = {key: value for key, value in row.items()
                      if key != "content_hash"}
            values["batch_id"] = base.batch_id
            values["content_hash"] = _digest(values)
            reservation_reasons.append(values)
    if len(pinned) > 1:
        raise ValueError("V3 coalescing cannot mix market plan/query authority")
    if len(episodes) > len(base.events):
        raise ValueError("V3 squeeze children exceed parent events")
    return V3SqueezeBatch(base, tuple(episodes), tuple(reservation_reasons))


def seal_squeeze_family_v3(
    v2_commit: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
    parent_events: Sequence[Mapping[str, Any]],
    *, reservation_reasons: Sequence[Mapping[str, Any]] = (),
    parent_reservations: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Produce a replacement V3 seal after exact parent/child verification.

    Parent events must already be verified by the ordinary V2 event seal.
    This pure function never upgrades an existing V2 commit in storage.
    """
    if set(v2_commit) != _COMMIT_COLUMNS - {
        "backtest_squeeze_episode_count", "backtest_squeeze_episode_hash",
        "portfolio_reservation_reason_count", "portfolio_reservation_reason_hash"}:
        raise ValueError("V2 commit columns differ from V3 base")
    batch = str(UUID(str(v2_commit["batch_id"])))
    run = str(v2_commit["run_id"])
    event_by_id = {str(UUID(str(e["record_id"]))): e for e in parent_events}
    if len(event_by_id) != len(parent_events):
        raise ValueError("Duplicate parent event")
    required = {identity for identity, event in event_by_id.items()
                if (event.get("category"), event.get("entity_type")) ==
                ("market_discovery_signal", "signal_occurrence")}
    seen: set[str] = set()
    identities: list[tuple[str, str]] = []
    for row in rows:
        if set(row) != _ROW_COLUMNS:
            raise ValueError("Squeeze row columns differ from V3 contract")
        canonical = _canonical_row({k: v for k, v in row.items() if k != "content_hash"})
        identity = canonical["record_id"]
        if identity in seen or row["content_hash"] != _digest(canonical):
            raise ValueError("Duplicate or corrupt squeeze row")
        seen.add(identity)
        parent = event_by_id.get(identity)
        if parent is None or str(parent.get("category")) != "market_discovery_signal" or str(parent.get("entity_type")) != "signal_occurrence":
            raise ValueError("Squeeze row lacks typed parent occurrence")
        if (canonical["run_id"] != run or canonical["batch_id"] != batch
                or str(parent.get("run_id")) != run
                or str(UUID(str(parent.get("batch_id")))) != batch
                or str(parent.get("entity_id")) != canonical["episode_id"]
                or str(parent.get("account_id")) != canonical["account_id"]
                or ("event_month" in parent and
                    str(parent["event_month"]) != canonical["event_month"])):
            raise ValueError("Squeeze parent identity differs")
        event_at = datetime.fromisoformat(str(parent["event_time"]).replace("Z", "+00:00"))
        start_at = datetime.fromisoformat(canonical["episode_started_at"]).replace(tzinfo=timezone.utc)
        expires_at = datetime.fromisoformat(canonical["expires_at"]).replace(tzinfo=timezone.utc)
        if (event_at.tzinfo is None or event_at.astimezone(timezone.utc) != start_at
                or expires_at != start_at + timedelta(seconds=300)
                or canonical["event_month"] != start_at.date().replace(day=1).isoformat()
                or canonical["signal_stream_id"] != STREAM_ID
                or canonical["source_authority"] != CONTRACT
                or not _HEX.fullmatch(canonical["market_plan_token"])
                or not _HEX.fullmatch(canonical["query_sha256"])):
            raise ValueError("Squeeze completed-boundary causality differs")
        identities.append((identity, str(row["content_hash"])))
    if seen != required:
        raise ValueError("Committed squeeze occurrence lacks one typed child")
    sealed = dict(v2_commit)
    sealed["backtest_squeeze_episode_count"] = len(rows)
    sealed["backtest_squeeze_episode_hash"] = _digest(sorted(identities))
    sealed.update(seal_reservation_reason_family_v3(
        reservation_reasons, parent_events, parent_reservations,
        run_id=run, batch_id=batch))
    return sealed


def verify_squeeze_family_v3(
    commit: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
    parent_events: Sequence[Mapping[str, Any]],
    *, stored_utc: bool = False,
    reservation_reasons: Sequence[Mapping[str, Any]] = (),
    parent_reservations: Sequence[Mapping[str, Any]] = (),
) -> tuple[dict[str, Any], ...]:
    """Verify V3 family seal before exposing a bounded typed occurrence page."""
    if set(commit) != _COMMIT_COLUMNS:
        raise ValueError("Not an exact V3 commit")
    base = {k: v for k, v in commit.items() if k not in {
        "backtest_squeeze_episode_count", "backtest_squeeze_episode_hash",
        "portfolio_reservation_reason_count", "portfolio_reservation_reason_hash"}}
    normalized = []
    for row in rows:
        if stored_utc:
            converted = dict(row)
            for field in ("episode_started_at", "expires_at"):
                converted[field] = str(converted[field]) + "+00:00"
            normalized.append(converted)
        else:
            normalized.append(dict(row))
    expected = seal_squeeze_family_v3(
        base, normalized, parent_events,
        reservation_reasons=reservation_reasons,
        parent_reservations=parent_reservations)
    if (type(commit["backtest_squeeze_episode_count"]) is not int
            or expected != dict(commit)):
        raise ValueError("V3 squeeze count/hash or base commit differs")
    return tuple(normalized)


def load_verified_squeeze_v3_prefix(
    client: Any, run_id: str, *, expected_market_plan_token: str,
    expected_query_sha256: str,
) -> V3CommittedPrefix | None:
    """Cold read an entire V3-only chain; never reinterpret a V2 fence.

    This inactive reader verifies every shared typed family before returning
    the closed squeeze occurrence family. Uncommitted future fact rows are not
    part of the returned prefix; a future V3 commit is never ignored.
    """
    if not run_id:
        raise ValueError("V3 run identity is required")
    if (_HEX.fullmatch(expected_market_plan_token) is None
            or _HEX.fullmatch(expected_query_sha256) is None):
        raise ValueError("V3 squeeze reader requires pinned market authority")
    contracts = versioned_journal_v2_contracts() + (
                          SQUEEZE_EPISODE, RESERVATION_REASON,
                          SQUEEZE_COMMIT_V3)
    storage_preflight(client, tables=contracts)
    for fence in ("trading_commit_v1", "trading_commit_v2"):
        if _rows(client, f"SELECT batch_id FROM arte.{fence} "
                 f"WHERE run_id={_literal(run_id)} LIMIT 1 FORMAT JSONEachRow"):
            raise RuntimeError("V3 run cannot mix V1/V2 commits")
    columns = ",".join(name for name, _ in SQUEEZE_COMMIT_V3.columns)
    commits = _rows(client,
        f"SELECT {columns} FROM arte.trading_commit_v3 "
        f"WHERE run_id={_literal(run_id)} ORDER BY last_sequence,batch_id FORMAT JSONEachRow")
    if not commits:
        return None
    prior_id = "00000000-0000-0000-0000-000000000000"
    prior_sequence = 0
    prior_status = "running"
    result: list[dict[str, Any]] = []
    batch_ids: list[str] = []
    for commit in commits:
        if set(commit) != _COMMIT_COLUMNS:
            raise RuntimeError("V3 commit columns differ")
        batch = str(UUID(str(commit["batch_id"])))
        first, last = int(commit["first_sequence"]), int(commit["last_sequence"])
        if (commit["run_id"] != run_id or str(UUID(str(commit["prior_batch_id"]))) != prior_id
                or first != prior_sequence + 1 or last < first
                or int(commit["event_count"]) != last - first + 1
                or prior_status != "running" or commit["status"] not in
                {"running", "completed", "stopped", "failed"}
                or not isinstance(commit["source_cursor"], str)
                or not commit["source_cursor"]
                or commit["source_cursor"].lstrip().startswith(("{", "["))):
            raise RuntimeError("V3 commit chain is not contiguous or causal")
        # Existing typed-family verifier checks every V2-era fact hash against
        # this V3 seal's inherited count/hash columns, not a V2 commit row.
        _verify_recovery_chunk(client, [commit], journal_profile="backtest_v2")
        ids = f"batch_id=toUUID({_literal(batch)})"
        parents = _rows(client,
            "SELECT record_id,run_id,event_month,batch_id,category,entity_type,entity_id,"
            "account_id,event_time,sequence FROM arte.trading_event_v1 "
            f"WHERE {ids} "
            "AND category='market_discovery_signal' "
            "AND entity_type='signal_occurrence' FORMAT JSONEachRow")
        if any(not first <= int(parent["sequence"]) <= last for parent in parents):
            raise RuntimeError("Squeeze parent lies beyond committed causal prefix")
        for parent in parents:
            clock = str(parent["event_time"])
            match = re.fullmatch(
                r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6})(\d{3})", clock)
            if match is None or match.group(2) != "000":
                raise RuntimeError("Squeeze parent has non-microsecond completed clock")
            parent["event_time"] = match.group(1) + "+00:00"
        reservation_events = _rows(client,
            "SELECT record_id,run_id,event_month,batch_id,category,entity_type,entity_id,"
            "account_id,event_time,sequence FROM arte.trading_event_v1 "
            f"WHERE {ids} AND category='portfolio_management' "
            "AND entity_type='portfolio_reservation' FORMAT JSONEachRow")
        if any(not first <= int(parent["sequence"]) <= last
               for parent in reservation_events):
            raise RuntimeError("Reservation parent lies beyond committed causal prefix")
        reservation_parents = _rows(client,
            "SELECT record_id,run_id,event_month,batch_id,account_id,reservation_id,event "
            "FROM arte.trading_portfolio_reservation_event_v1 "
            f"WHERE {ids} FORMAT JSONEachRow")
        reason_columns = ",".join(name for name, _ in RESERVATION_REASON.columns)
        reasons = _rows(client,
            f"SELECT {reason_columns} FROM arte.{RESERVATION_REASON.name} "
            f"WHERE {ids} FORMAT JSONEachRow")
        projections = ",".join(
            f"toString({name}) AS {name}" if kind.startswith("Decimal") else name
            for name, kind in SQUEEZE_EPISODE.columns)
        children = _rows(client,
            f"SELECT {projections} FROM arte.{SQUEEZE_EPISODE.name} "
            f"WHERE {ids} FORMAT JSONEachRow")
        verified = verify_squeeze_family_v3(
            commit, children, parents + reservation_events, stored_utc=True,
            reservation_reasons=reasons,
            parent_reservations=reservation_parents)
        if any(row["market_plan_token"] != expected_market_plan_token
               or row["query_sha256"] != expected_query_sha256 for row in verified):
            raise RuntimeError("V3 squeeze row differs from pinned market authority")
        result.extend(verified)
        batch_ids.append(batch)
        prior_id, prior_sequence, prior_status = batch, last, str(commit["status"])
    return V3CommittedPrefix(
        run_id, prior_sequence, prior_id, str(commits[-1]["source_cursor"]),
        prior_status, tuple(batch_ids), tuple(result))


def load_verified_squeeze_v3_run(
    client: Any, run_id: str, *, expected_market_plan_token: str,
    expected_query_sha256: str,
) -> tuple[dict[str, Any], ...]:
    """Expose only a terminal V3 chain; running prefixes are restart-only."""
    prefix = load_verified_squeeze_v3_prefix(
        client, run_id, expected_market_plan_token=expected_market_plan_token,
        expected_query_sha256=expected_query_sha256)
    if prefix is None:
        return ()
    if prefix.status == "running":
        raise RuntimeError("V3 Signal Stream requires a terminal committed run")
    return prefix.occurrences
