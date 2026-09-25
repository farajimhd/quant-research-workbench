"""Inactive typed dispatch intent and activation-ACK cursor contract.

These arte tables are operator-only definitions. A local queue return is not an
activation ACK; ACK rows require the durable activation writer receipt hash.
No INSERT or DDL is executed by this module.
"""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Mapping, Protocol

from src.backend.signal_stream_typed_cursor import TypedTable
from src.trading_runtime.journal_contract import canonical_json


_KEY = (("schema_version", "UInt16"), ("session_key", "Date"),
        ("source_batch_sequence", "UInt64"))
INTENT = TypedTable(
    "signal_dispatch_intent_typed_v1",
    _KEY + (("ordinal", "UInt32"), ("delivery_id", "String"),
            ("run_plan_id", "String"), ("profile_id", "String"),
            ("book_id", "String"), ("ticker", "String"),
            ("signal_stream_id", "String"), ("event_id", "FixedString(64)"),
            ("event_time", "DateTime64(6, 'UTC')"),
            ("configuration_revision_id", "String"),
            ("source_cursor_commit_hash", "FixedString(64)"),
            ("content_hash", "FixedString(64)")),
    "session_key, source_batch_sequence, ordinal",
)
INTENT_COMMIT = TypedTable(
    "signal_dispatch_intent_commit_typed_v1",
    _KEY + (("source_cursor_commit_hash", "FixedString(64)"),
            ("configuration_revision_id", "String"),
            ("intent_count", "UInt32"), ("intent_hash", "FixedString(64)"),
            ("content_hash", "FixedString(64)")),
    "session_key, source_batch_sequence",
)
ACK = TypedTable(
    "signal_dispatch_ack_typed_v1",
    _KEY + (("ordinal", "UInt32"), ("delivery_id", "String"),
            ("ack_kind", "LowCardinality(String)"),
            ("activation_receipt_hash", "FixedString(64)"),
            ("acknowledged_at", "DateTime64(6, 'UTC')"),
            ("content_hash", "FixedString(64)")),
    "session_key, source_batch_sequence, ordinal",
)
ACK_COMMIT = TypedTable(
    "signal_dispatch_ack_commit_typed_v1",
    _KEY + (("intent_commit_hash", "FixedString(64)"),
            ("ack_count", "UInt32"), ("ack_hash", "FixedString(64)"),
            ("content_hash", "FixedString(64)")),
    "session_key, source_batch_sequence",
)
DISPATCH_TABLES = (INTENT, INTENT_COMMIT, ACK, ACK_COMMIT)
_DELIVERY_KEYS = frozenset({
    "delivery_id", "run_plan_id", "profile_id", "book_id", "ticker",
    "signal_stream_id", "event_id", "event_time", "occurrence",
})


class OccurrenceAuthority(Protocol):
    def read_exact(self, event_id: str) -> Mapping[str, Any] | None: ...


class DispatchColdStorage(Protocol):
    def read_dispatch_rows(self, table_name: str, *, session_key: str,
                           source_batch_sequence: int) -> list[Mapping[str, Any]]: ...
    def list_dispatch_commits(self, table_name: str, *, session_key: str) -> list[Mapping[str, Any]]: ...


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _seal(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": _hash(row)}


def _hex(value: Any) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ValueError("dispatch authority hash must be lowercase SHA-256")
    return value


def _time(value: Any) -> str:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
        str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("dispatch event time must be timezone-aware")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds")


def project_dispatch_intents(
    deliveries: list[Mapping[str, Any]], *, session_key: str,
    source_batch_sequence: int, source_cursor_commit_hash: str,
    configuration_revision_id: str, occurrence_authority: OccurrenceAuthority,
) -> dict[str, Any]:
    """Freeze exact selected deliveries; zero-delivery fence advances cursor."""
    if (not isinstance(session_key, str) or len(session_key) != 10
            or datetime.fromisoformat(session_key).date().isoformat() != session_key
            or type(source_batch_sequence) is not int or source_batch_sequence < 1
            or not isinstance(configuration_revision_id, str)
            or not configuration_revision_id or len(deliveries) > 100_000):
        raise ValueError("dispatch source identity is invalid")
    source_hash = _hex(source_cursor_commit_hash)
    rows = []
    for ordinal, delivery in enumerate(deliveries):
        if not isinstance(delivery, Mapping) or set(delivery) != _DELIVERY_KEYS:
            raise ValueError("dispatch delivery has unmodeled fields")
        event_id = _hex(delivery["event_id"])
        occurrence = occurrence_authority.read_exact(event_id)
        if occurrence is None or dict(occurrence) != dict(delivery["occurrence"]):
            raise ValueError("dispatch occurrence lacks exact typed source authority")
        run_plan_id = delivery["run_plan_id"]
        if (not isinstance(run_plan_id, str) or not run_plan_id
                or delivery["delivery_id"] != f"{run_plan_id}:{event_id}"
                or any(not isinstance(delivery[key], str) or not delivery[key]
                       for key in ("profile_id", "book_id", "ticker", "signal_stream_id"))
                or delivery["ticker"] != occurrence.get("ticker")
                or delivery["signal_stream_id"] != occurrence.get("signal_stream_id")
                or _time(delivery["event_time"]) != _time(
                    occurrence.get("effective_at") or occurrence.get("event_time"))):
            raise ValueError("dispatch delivery identity or causal time differs")
        rows.append(_seal(dict(
            schema_version=1, session_key=session_key,
            source_batch_sequence=source_batch_sequence, ordinal=ordinal,
            delivery_id=delivery["delivery_id"], run_plan_id=run_plan_id,
            profile_id=delivery["profile_id"], book_id=delivery["book_id"],
            ticker=delivery["ticker"], signal_stream_id=delivery["signal_stream_id"],
            event_id=event_id, event_time=_time(delivery["event_time"]),
            configuration_revision_id=configuration_revision_id,
            source_cursor_commit_hash=source_hash,
        )))
    if rows != sorted(rows, key=lambda row: (
            row["event_time"], row["run_plan_id"], row["event_id"])):
        raise ValueError("dispatch deliveries are not in canonical order")
    if len({row["delivery_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate dispatch delivery identity")
    commit = _seal(dict(
        schema_version=1, session_key=session_key,
        source_batch_sequence=source_batch_sequence,
        source_cursor_commit_hash=source_hash,
        configuration_revision_id=configuration_revision_id,
        intent_count=len(rows), intent_hash=_hash(rows),
    ))
    return {"intents": rows, "commit": commit}


def project_dispatch_ack(
    intents: Mapping[str, Any], receipts: list[Mapping[str, Any]], *,
    acknowledged_at: Any,
) -> dict[str, Any]:
    """One durable activation receipt per intent; never local queue accepts."""
    expected = intents.get("intents")
    commit = intents.get("commit")
    if not isinstance(expected, list) or not isinstance(commit, Mapping):
        raise ValueError("dispatch intent fence is required")
    if (commit.get("intent_count") != len(expected)
            or commit.get("intent_hash") != _hash(expected)
            or commit != _seal({key: value for key, value in commit.items()
                                if key != "content_hash"})
            or len(receipts) != len(expected)):
        raise ValueError("dispatch intent or ACK count differs")
    rows = []
    for intent, receipt in zip(expected, receipts, strict=True):
        if (set(receipt) != {"delivery_id", "ack_kind", "activation_receipt_hash"}
                or receipt["delivery_id"] != intent["delivery_id"]
                or receipt["ack_kind"] != "activation_durable"):
            raise ValueError("dispatch ACK differs from ordered intent")
        rows.append(_seal(dict(
            schema_version=1, session_key=commit["session_key"],
            source_batch_sequence=commit["source_batch_sequence"],
            ordinal=intent["ordinal"], delivery_id=intent["delivery_id"],
            ack_kind=receipt["ack_kind"],
            activation_receipt_hash=_hex(receipt["activation_receipt_hash"]),
            acknowledged_at=_time(acknowledged_at),
        )))
    return {"acks": rows, "commit": _seal(dict(
        schema_version=1, session_key=commit["session_key"],
        source_batch_sequence=commit["source_batch_sequence"],
        intent_commit_hash=_hex(commit["content_hash"]),
        ack_count=len(rows), ack_hash=_hash(rows),
    ))}


def verify_dispatch_cursor(intents: Mapping[str, Any], acks: Mapping[str, Any]) -> None:
    """Cold exact verification; no FINAL or implicit duplicate suppression."""
    intent_rows, intent_commit = intents["intents"], intents["commit"]
    ack_rows, ack_commit = acks["acks"], acks["commit"]
    if (intent_commit != _seal({key: value for key, value in intent_commit.items()
                                if key != "content_hash"})
            or intent_commit["intent_count"] != len(intent_rows)
            or intent_commit["intent_hash"] != _hash(intent_rows)
            or ack_commit != _seal({key: value for key, value in ack_commit.items()
                                    if key != "content_hash"})
            or ack_commit["intent_commit_hash"] != intent_commit["content_hash"]
            or ack_commit["ack_count"] != len(ack_rows) or len(ack_rows) != len(intent_rows)
            or ack_commit["ack_hash"] != _hash(ack_rows)):
        raise ValueError("dispatch cursor commit fence differs")
    for ordinal, (intent, ack) in enumerate(zip(intent_rows, ack_rows, strict=True)):
        if (intent != _seal({key: value for key, value in intent.items()
                            if key != "content_hash"})
                or ack != _seal({key: value for key, value in ack.items()
                                 if key != "content_hash"})
                or intent["ordinal"] != ordinal or ack["ordinal"] != ordinal
                or ack["delivery_id"] != intent["delivery_id"]
                or ack["ack_kind"] != "activation_durable"
                or _hex(ack["activation_receipt_hash"]) != ack["activation_receipt_hash"]
                or intent["source_cursor_commit_hash"]
                != intent_commit["source_cursor_commit_hash"]
                or intent["configuration_revision_id"]
                != intent_commit["configuration_revision_id"]
                or (intent["session_key"], intent["source_batch_sequence"])
                != (intent_commit["session_key"], intent_commit["source_batch_sequence"])
                or (ack["session_key"], ack["source_batch_sequence"])
                != (intent_commit["session_key"], intent_commit["source_batch_sequence"])):
            raise ValueError("dispatch cursor row identity or order differs")


def read_committed_dispatch_prefix(
    storage: DispatchColdStorage, *, session_key: str,
    source_commit_hashes: tuple[str, ...], configuration_revision_id: str,
) -> tuple[tuple[dict[str, Any], dict[str, Any]], ...]:
    """Verify every dispatch batch through a separately verified source prefix.

    This is not a session-completeness proof: the source head must be sealed
    externally before its prefix can be used as a cold-start coverage bound.
    """
    from src.backend.signal_stream_typed_readback import canonical_row

    if (not isinstance(session_key, str) or not session_key
            or not isinstance(configuration_revision_id, str)
            or not configuration_revision_id):
        raise ValueError("dispatch cold scope is invalid")
    hashes = tuple(_hex(value) for value in source_commit_hashes)
    if not hashes:
        raise ValueError("dispatch cold prefix lacks a sealed nonempty source bound")
    listed_intents = [canonical_row(INTENT_COMMIT, row) for row in
                      storage.list_dispatch_commits(INTENT_COMMIT.name, session_key=session_key)]
    listed_acks = [canonical_row(ACK_COMMIT, row) for row in
                   storage.list_dispatch_commits(ACK_COMMIT.name, session_key=session_key)]
    expected_sequences = list(range(1, len(hashes) + 1))
    for listed in (listed_intents, listed_acks):
        if sorted(row["source_batch_sequence"] for row in listed) != expected_sequences:
            raise ValueError("dispatch cold commits are missing, duplicate, or beyond source prefix")
    recovered = []
    for sequence, source_hash in enumerate(hashes, 1):
        families = []
        for table, commits in ((INTENT, listed_intents), (INTENT_COMMIT, listed_intents),
                               (ACK, listed_acks), (ACK_COMMIT, listed_acks)):
            rows = [canonical_row(table, row) for row in storage.read_dispatch_rows(
                table.name, session_key=session_key, source_batch_sequence=sequence)]
            if table in (INTENT_COMMIT, ACK_COMMIT):
                if len(rows) != 1 or rows[0] != next(
                        row for row in commits if row["source_batch_sequence"] == sequence):
                    raise ValueError("dispatch cold commit listing differs from exact read")
                families.append(rows[0])
            else:
                families.append(sorted(rows, key=lambda row: row["ordinal"]))
        intent_rows, intent_commit, ack_rows, ack_commit = families
        if (intent_commit["session_key"] != session_key
                or intent_commit["source_batch_sequence"] != sequence
                or ack_commit["session_key"] != session_key
                or ack_commit["source_batch_sequence"] != sequence
                or intent_commit["source_cursor_commit_hash"] != source_hash
                or intent_commit["configuration_revision_id"] != configuration_revision_id):
            raise ValueError("dispatch cold commit differs from verified source")
        intents = {"intents": intent_rows, "commit": intent_commit}
        acks = {"acks": ack_rows, "commit": ack_commit}
        verify_dispatch_cursor(intents, acks)
        recovered.append((intents, acks))
    return tuple(recovered)
