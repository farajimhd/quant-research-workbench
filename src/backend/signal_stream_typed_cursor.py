"""Inactive typed Signal Stream state delta and occurrence-reference fence.

This is not an occurrence payload store. Cold recovery requires an independent
typed occurrence authority able to return the exact event by ID. In particular,
the current SQLite Signal Stream is not a valid authority for this contract.
No DDL or INSERT is executed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Any, Mapping, Protocol
from zoneinfo import ZoneInfo

from src.trading_runtime.journal_contract import canonical_json


NY = ZoneInfo("America/New_York")
VERSION = 1


@dataclass(frozen=True)
class TypedTable:
    name: str
    columns: tuple[tuple[str, str], ...]
    order: str

    def ddl(self) -> str:
        columns = ", ".join(f"{name} {kind}" for name, kind in self.columns)
        return (f"CREATE TABLE IF NOT EXISTS arte.{self.name} ({columns}) "
                f"ENGINE = MergeTree PARTITION BY toYYYYMM(session_key) "
                f"ORDER BY ({self.order}) SETTINGS storage_policy = 'live_market_ssd'")


_KEY = (("schema_version", "UInt16"), ("session_key", "Date"),
        ("batch_sequence", "UInt64"))
STATE_DELTA = TypedTable(
    "signal_stream_state_delta_typed_v1",
    _KEY + (("signal_stream_id", "String"), ("ticker", "String"),
            ("operation", "LowCardinality(String)"), ("matching", "Nullable(Bool)"),
            ("definition_revision", "Nullable(String)"),
            ("last_emitted_at", "Nullable(DateTime64(6, 'UTC'))"),
            ("content_hash", "FixedString(64)")),
    "session_key, batch_sequence, signal_stream_id, ticker",
)
OCCURRENCE_REF = TypedTable(
    "signal_stream_occurrence_ref_typed_v1",
    _KEY + (("ordinal", "UInt32"), ("event_id", "FixedString(64)"),
            ("signal_stream_id", "String"), ("ticker", "String"),
            ("definition_revision", "String"),
            ("event_time", "DateTime64(6, 'UTC')"),
            ("source_content_hash", "FixedString(64)"),
            ("content_hash", "FixedString(64)")),
    "session_key, batch_sequence, ordinal",
)
COMMIT = TypedTable(
    "signal_stream_cursor_commit_typed_v1",
    _KEY + (("configuration_revision", "String"),
            ("source_revision", "String"),
            ("cutoff_at", "DateTime64(6, 'UTC')"),
            ("previous_commit_hash", "FixedString(64)"),
            ("state_delta_count", "UInt32"),
            ("state_delta_hash", "FixedString(64)"),
            ("occurrence_count", "UInt32"),
            ("occurrence_hash", "FixedString(64)"),
            ("content_hash", "FixedString(64)")),
    "session_key, batch_sequence",
)
TABLES = (STATE_DELTA, OCCURRENCE_REF, COMMIT)


class TypedOccurrenceAuthority(Protocol):
    def read_exact(self, event_id: str) -> Mapping[str, Any] | None: ...


class CursorRowReader(Protocol):
    def read_rows(self, table_name: str, *, session_key: str,
                  batch_sequence: int) -> list[Mapping[str, Any]]: ...


def read_cursor_batch(reader: CursorRowReader, *, session_key: str,
                      batch_sequence: int) -> dict[str, Any]:
    """Read raw rows without FINAL/dedup assumptions; reject duplicate fence."""
    commits = reader.read_rows(COMMIT.name, session_key=session_key,
                               batch_sequence=batch_sequence)
    if len(commits) != 1:
        raise ValueError("missing or duplicate Signal Stream commit fence")
    return {
        "state_delta": reader.read_rows(STATE_DELTA.name, session_key=session_key,
                                        batch_sequence=batch_sequence),
        "occurrence_ref": reader.read_rows(OCCURRENCE_REF.name, session_key=session_key,
                                           batch_sequence=batch_sequence),
        "commit": commits[0],
    }


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _seal(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": _hash(row)}


def _time(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("timestamp must be an ISO timestamp")
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds")


def _state(states: Mapping[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    if not isinstance(states, Mapping):
        raise ValueError("Signal Stream state must be mapping")
    normalized = {}
    for stream_id, tickers in states.items():
        if (not isinstance(stream_id, str) or not stream_id
                or not isinstance(tickers, Mapping) or not tickers):
            raise ValueError("invalid Signal Stream state identity")
        normalized[stream_id] = {}
        for ticker, value in tickers.items():
            if (not isinstance(ticker, str) or not ticker or ticker != ticker.upper()
                    or not isinstance(value, Mapping)
                    or set(value) - {"matching", "definition_revision", "last_emitted_at"}
                    or not {"matching", "definition_revision"} <= set(value)
                    or type(value["matching"]) is not bool
                    or not isinstance(value["definition_revision"], str)
                    or not value["definition_revision"]):
                raise ValueError("unmodeled Signal Stream state row")
            normalized[stream_id][ticker] = {
                "matching": value["matching"],
                "definition_revision": value["definition_revision"],
                **({"last_emitted_at": _time(value["last_emitted_at"])}
                   if "last_emitted_at" in value else {}),
            }
    return normalized


def project_cursor_batch(
    before_states: Mapping[str, Any], after_states: Mapping[str, Any],
    occurrences: list[Mapping[str, Any]], *, session_key: str,
    batch_sequence: int, cutoff_at: Any, configuration_revision: str,
    source_revision: str, previous_commit_hash: str,
    before_admissions: Mapping[str, Any] | None = None,
    after_admissions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build compact deltas then a late commit fence; no I/O."""
    if before_admissions or after_admissions:
        raise ValueError("typed watchlist admission state is not modeled")
    if (date.fromisoformat(session_key).isoformat() != session_key
            or type(batch_sequence) is not int or batch_sequence < 1
            or not configuration_revision or not source_revision
            or len(previous_commit_hash) != 64):
        raise ValueError("invalid Signal Stream cursor identity")
    cutoff = _time(cutoff_at)
    if datetime.fromisoformat(cutoff).astimezone(NY).date().isoformat() != session_key:
        raise ValueError("cursor cutoff outside session")
    before, after = _state(before_states), _state(after_states)
    if any(_time(row["last_emitted_at"]) > cutoff
           for states in (before, after) for stream in states.values()
           for row in stream.values() if "last_emitted_at" in row):
        raise ValueError("Signal Stream state exceeds causal cutoff")
    common = dict(schema_version=VERSION, session_key=session_key,
                  batch_sequence=batch_sequence)
    deltas = []
    keys = sorted({(stream, ticker) for stream, rows in before.items() for ticker in rows}
                  | {(stream, ticker) for stream, rows in after.items() for ticker in rows})
    for stream, ticker in keys:
        prior = before.get(stream, {}).get(ticker)
        current = after.get(stream, {}).get(ticker)
        if current == prior:
            continue
        deltas.append(_seal({**common, "signal_stream_id": stream, "ticker": ticker,
                             "operation": "set" if current is not None else "delete",
                             "matching": current["matching"] if current else None,
                             "definition_revision": current["definition_revision"] if current else None,
                             "last_emitted_at": current.get("last_emitted_at") if current else None}))
    if not isinstance(occurrences, list):
        raise ValueError("occurrences must be ordered list")
    refs = []
    seen = set()
    for ordinal, occurrence in enumerate(occurrences):
        if not isinstance(occurrence, Mapping):
            raise ValueError("invalid occurrence")
        event_id = occurrence.get("event_id")
        if (not isinstance(event_id, str) or len(event_id) != 64
                or any(char not in "0123456789abcdef" for char in event_id)
                or occurrence.get("signal_id") != event_id or event_id in seen):
            raise ValueError("missing or duplicate occurrence identity")
        seen.add(event_id)
        event_time = _time(occurrence.get("event_time"))
        if (_time(occurrence.get("available_at")) > event_time
                or event_time > cutoff
                or datetime.fromisoformat(event_time).astimezone(NY).date().isoformat() != session_key):
            raise ValueError("noncausal occurrence time")
        for key in ("signal_stream_id", "ticker", "definition_revision"):
            if not isinstance(occurrence.get(key), str) or not occurrence[key]:
                raise ValueError("incomplete occurrence source revision")
        if occurrence["ticker"] != occurrence["ticker"].upper():
            raise ValueError("occurrence ticker is not normalized")
        refs.append(_seal({**common, "ordinal": ordinal, "event_id": event_id,
                           "signal_stream_id": occurrence["signal_stream_id"],
                           "ticker": occurrence["ticker"],
                           "definition_revision": occurrence["definition_revision"],
                           "event_time": event_time,
                           "source_content_hash": _hash(dict(occurrence))}))
    fence = _seal({**common, "configuration_revision": configuration_revision,
                   "source_revision": source_revision, "cutoff_at": cutoff,
                   "previous_commit_hash": previous_commit_hash,
                   "state_delta_count": len(deltas), "state_delta_hash": _hash(deltas),
                   "occurrence_count": len(refs), "occurrence_hash": _hash(refs)})
    return {"state_delta": deltas, "occurrence_ref": refs, "commit": fence}


def recover_cursor_batch(
    before_states: Mapping[str, Any], rows: Mapping[str, Any], *,
    occurrence_authority: TypedOccurrenceAuthority, session_key: str,
    expected_sequence: int, previous_commit_hash: str,
    configuration_revision: str, source_revision: str,
) -> tuple[dict[str, dict[str, dict[str, Any]]], list[dict[str, Any]], str]:
    """Read-only cold verifier; rejects partial, duplicate and mixed batches."""
    if not isinstance(rows, Mapping) or set(rows) != {"state_delta", "occurrence_ref", "commit"}:
        raise ValueError("incomplete typed Signal Stream batch")
    deltas, refs, fence = rows["state_delta"], rows["occurrence_ref"], rows["commit"]
    if not isinstance(deltas, list) or not isinstance(refs, list) or not isinstance(fence, Mapping):
        raise ValueError("invalid typed Signal Stream row families")
    common = dict(schema_version=VERSION, session_key=session_key,
                  batch_sequence=expected_sequence)
    if (any(fence.get(key) != value for key, value in common.items())
            or fence.get("previous_commit_hash") != previous_commit_hash
            or fence.get("configuration_revision") != configuration_revision
            or fence.get("source_revision") != source_revision
            or fence.get("state_delta_count") != len(deltas)
            or fence.get("occurrence_count") != len(refs)
            or fence.get("state_delta_hash") != _hash(deltas)
            or fence.get("occurrence_hash") != _hash(refs)
            or fence != _seal({key: value for key, value in fence.items() if key != "content_hash"})):
        raise ValueError("typed Signal Stream commit fence mismatch")
    after = _state(before_states)
    observed_keys = set()
    for row in deltas:
        if (not isinstance(row, Mapping) or set(row) != {name for name, _ in STATE_DELTA.columns}
                or any(row[key] != value for key, value in common.items())
                or row != _seal({key: value for key, value in row.items() if key != "content_hash"})):
            raise ValueError("invalid Signal Stream state delta")
        key = (row["signal_stream_id"], row["ticker"])
        if key in observed_keys or observed_keys and key <= max(observed_keys):
            raise ValueError("duplicate or unordered Signal Stream state delta")
        observed_keys.add(key)
        if row["operation"] == "delete":
            if (row["matching"] is not None or row["definition_revision"] is not None
                    or row["last_emitted_at"] is not None
                    or key[1] not in after.get(key[0], {})):
                raise ValueError("invalid Signal Stream deletion")
            del after[key[0]][key[1]]
            if not after[key[0]]:
                del after[key[0]]
        elif row["operation"] == "set":
            value = {"matching": row["matching"],
                     "definition_revision": row["definition_revision"],
                     **({"last_emitted_at": row["last_emitted_at"]}
                        if row["last_emitted_at"] is not None else {})}
            _state({key[0]: {key[1]: value}})
            after.setdefault(key[0], {})[key[1]] = value
        else:
            raise ValueError("unmodeled Signal Stream delta operation")
    occurrences = []
    seen = set()
    for ordinal, row in enumerate(refs):
        if (not isinstance(row, Mapping) or set(row) != {name for name, _ in OCCURRENCE_REF.columns}
                or any(row[key] != value for key, value in common.items())
                or row["ordinal"] != ordinal or row["event_id"] in seen
                or row != _seal({key: value for key, value in row.items() if key != "content_hash"})):
            raise ValueError("missing or duplicate occurrence reference")
        seen.add(row["event_id"])
        occurrence = occurrence_authority.read_exact(row["event_id"])
        if occurrence is None or _hash(dict(occurrence)) != row["source_content_hash"]:
            raise ValueError("typed occurrence authority missing or conflicting")
        occurrences.append(dict(occurrence))
    if project_cursor_batch(
        before_states, after, occurrences, session_key=session_key,
        batch_sequence=expected_sequence, cutoff_at=fence["cutoff_at"],
        configuration_revision=configuration_revision, source_revision=source_revision,
        previous_commit_hash=previous_commit_hash,
    ) != rows:
        raise ValueError("typed Signal Stream batch is noncanonical")
    return after, occurrences, fence["content_hash"]


def recover_cursor_chain(
    batches: list[Mapping[str, Any]], *, occurrence_authority: TypedOccurrenceAuthority,
    session_key: str, configuration_revision: str, source_revision: str,
    initial_commit_hash: str = "0" * 64,
) -> tuple[dict[str, dict[str, dict[str, Any]]], list[dict[str, Any]], str]:
    """Verify contiguous cold batches and reject repeated source events."""
    states: dict[str, dict[str, dict[str, Any]]] = {}
    replayed: list[dict[str, Any]] = []
    head = initial_commit_hash
    seen: set[str] = set()
    for sequence, rows in enumerate(batches, start=1):
        states, occurrences, head = recover_cursor_batch(
            states, rows, occurrence_authority=occurrence_authority,
            session_key=session_key, expected_sequence=sequence,
            previous_commit_hash=head,
            configuration_revision=configuration_revision,
            source_revision=source_revision,
        )
        ids = {row["event_id"] for row in occurrences}
        if ids & seen:
            raise ValueError("duplicate source occurrence across cursor batches")
        seen.update(ids)
        replayed.extend(occurrences)
    return states, replayed, head
