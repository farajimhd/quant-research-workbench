"""Typed checkpoint of broker states actually observed by Strategy 1 OMS.

The broker match snapshot may be ahead of OMS at a completed boundary. Never
seed OMS deduplication from broker state: doing so can suppress a pending
transition. Canonical-order and live-adapter fingerprints have distinct scalar
contracts so cold recovery can restore their exact deduplication tuple shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
from math import isfinite
from typing import Any, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.keeper_session import ManagedKeeperSession


ROOT = TableContract(
    "trading_strategy_one_oms_observation_snapshot_v2",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("session_date", "Date"),
     ("checkpoint_sequence", "UInt64"), ("boundary_ms", "UInt32"),
     ("observation_count", "UInt32"),
     ("observation_hash", "FixedString(64)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)", "run_id, checkpoint_sequence, snapshot_id",
)
OBSERVATION = TableContract(
    "trading_strategy_one_oms_observation_v2",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("checkpoint_sequence", "UInt64"),
     ("group_id", "String"), ("broker_order_id", "String"),
     ("fingerprint_kind", "LowCardinality(String)"),
     ("lifecycle_state", "String"), ("broker_status", "String"),
     ("filled_quantity", "Decimal(38, 18)"),
     ("remaining_quantity", "Decimal(38, 18)"),
     ("average_fill_price", "Decimal(38, 18)"),
     ("limit_price", "Decimal(38, 18)"),
     ("stop_price", "Decimal(38, 18)"),
     ("warning", "String"), ("rejection_code", "String"),
     ("rejection_reason", "String"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)",
    "run_id, checkpoint_sequence, group_id, broker_order_id",
)
TABLES = (ROOT, OBSERVATION)


@dataclass(frozen=True, slots=True)
class OmsObservationSnapshotRows:
    root: Mapping[str, Any]
    observations: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class OmsObservedGroup:
    broker_order_ids: tuple[str, ...]
    broker_order_state_fingerprints: Mapping[str, tuple[Any, ...]]


@dataclass(frozen=True, slots=True)
class OmsObservationHead:
    run_id: str
    checkpoint_sequence: int
    journal_batch_id: str
    snapshot_hash: str
    keeper_version: int


class ManagedOmsObservationHeadReader:
    """Read only the Keeper-selected root; never adopt orphan SQL rows."""

    def __init__(self, session: ManagedKeeperSession) -> None:
        if not isinstance(session, ManagedKeeperSession):
            raise TypeError("OMS observation head needs managed Keeper")
        self._session = session

    @staticmethod
    def path(run_id: str) -> str:
        if (type(run_id) is not str or not run_id
                or any(char in run_id for char in "\r\n\x00")):
            raise ValueError("OMS observation head run is invalid")
        return ("/trading/strategy-one-oms-observation/v2/"
                + sha256(run_id.encode()).hexdigest() + "/head")

    def read_head(self, *, run_id: str) -> OmsObservationHead:
        session, client = self._session, self._session.client
        if not session.writable or client.client_id is None:
            raise RuntimeError("OMS observation Keeper session is unavailable")
        generation, client_id = session._generation, client.client_id
        try:
            raw, stat = client.get(self.path(run_id))
            fields = raw.decode("utf-8").split("\n")
            if (len(fields) != 5 or fields[:2] != ["1", run_id]
                    or str(int(fields[2])) != fields[2] or int(fields[2]) < 1
                    or str(UUID(fields[3])) != fields[3]
                    or len(fields[4]) != 64
                    or any(char not in "0123456789abcdef" for char in fields[4])
                    or type(stat.version) is not int or stat.version < 0):
                raise ValueError
            head = OmsObservationHead(run_id, int(fields[2]), fields[3],
                                      fields[4], stat.version)
        except Exception as exc:
            raise ValueError("OMS observation Keeper head missing or corrupt") from exc
        if (not session.writable or session._generation != generation
                or client.client_id != client_id):
            raise RuntimeError("OMS observation Keeper session changed during read")
        return head


def _digest(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _sealed(value: Mapping[str, Any]) -> dict[str, Any]:
    content = dict(value)
    return {**content, "content_hash": _digest(content)}


def _number(value: Any) -> str:
    if type(value) not in (int, float, Decimal) or (
            type(value) is float and not isfinite(value)):
        raise ValueError("OMS observation has a nonfinite numeric field")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or abs(number) >= Decimal(10) ** 20:
            raise ValueError("OMS observation exceeds its decimal contract")
        with localcontext() as context:
            context.prec = 38
            return format(number.quantize(Decimal("0.000000000000000001")),
                          ".18f")
    except InvalidOperation as exc:
        raise ValueError("OMS observation exceeds its decimal scale") from exc


def project_oms_observation_snapshot(*, run_id: str, session_date: date,
                                     checkpoint_sequence: int,
                                     boundary_ms: int,
                                     groups: Mapping[str, Any]
                                     ) -> OmsObservationSnapshotRows:
    """Freeze only OMS-observed states, never current broker states."""
    if (not run_id or not isinstance(session_date, date)
            or type(checkpoint_sequence) is not int or checkpoint_sequence < 1
            or type(boundary_ms) is not int or not 0 <= boundary_ms <= 57_600_000
            or boundary_ms % 100 or not isinstance(groups, Mapping)):
        raise ValueError("OMS observation snapshot needs one completed boundary")
    snapshot_id = str(uuid5(NAMESPACE_URL,
                            f"{run_id}:{checkpoint_sequence}:oms-observation-v2"))
    common = {"snapshot_id": snapshot_id, "run_id": run_id,
              "snapshot_month": session_date.replace(day=1).isoformat(),
              "checkpoint_sequence": checkpoint_sequence}
    observations: list[dict[str, Any]] = []
    seen_orders: set[str] = set()
    for group_id, group in sorted(groups.items()):
        broker_ids = tuple(group.broker_order_ids)
        observed = group.broker_order_state_fingerprints
        if (not isinstance(group_id, str) or not group_id
                or len(set(broker_ids)) != len(broker_ids)
                or set(observed) - set(broker_ids)):
            raise ValueError("OMS observation has an unbound broker order")
        for broker_order_id, fingerprint in sorted(observed.items()):
            kind = ("canonical" if isinstance(fingerprint, tuple)
                    and len(fingerprint) == 10 else
                    "live_adapter" if isinstance(fingerprint, tuple)
                    and len(fingerprint) == 7 else "")
            if (not isinstance(broker_order_id, str) or not broker_order_id
                    or broker_order_id in seen_orders
                    or not isinstance(fingerprint, tuple)
                    or not kind
                    or type(fingerprint[0]) is not str or not fingerprint[0]
                    or any(type(fingerprint[index]) is not str
                           for index in ((1, 7, 8, 9) if kind == "canonical"
                                         else (6,)))):
                raise ValueError("OMS observation differs from its observed order")
            if kind == "live_adapter":
                fingerprint = ("", fingerprint[0], *fingerprint[1:6],
                               fingerprint[6], "", "")
            seen_orders.add(broker_order_id)
            observations.append(_sealed({
                **common, "group_id": group_id,
                "broker_order_id": broker_order_id,
                "fingerprint_kind": kind,
                "lifecycle_state": fingerprint[0],
                "broker_status": fingerprint[1],
                "filled_quantity": _number(fingerprint[2]),
                "remaining_quantity": _number(fingerprint[3]),
                "average_fill_price": _number(fingerprint[4]),
                "limit_price": _number(fingerprint[5]),
                "stop_price": _number(fingerprint[6]),
                "warning": fingerprint[7],
                "rejection_code": fingerprint[8],
                "rejection_reason": fingerprint[9],
            }))
    ordered = tuple(sorted(observations,
                           key=lambda row: (row["group_id"], row["broker_order_id"])))
    members = tuple((row["group_id"], row["broker_order_id"], row["content_hash"])
                    for row in ordered)
    root = _sealed({**common, "session_date": session_date.isoformat(),
                    "boundary_ms": boundary_ms,
                    "observation_count": len(ordered),
                    "observation_hash": _digest(members)})
    return OmsObservationSnapshotRows(root, ordered)


def verify_oms_observation_snapshot(
    rows: OmsObservationSnapshotRows,
) -> OmsObservationSnapshotRows:
    """Verify identity, row hashes and completeness before any actor install."""
    if not isinstance(rows, OmsObservationSnapshotRows):
        raise TypeError("OMS observation snapshot has no typed rows")
    root = rows.root
    if (set(root) != {name for name, _ in ROOT.columns}
            or _digest({key: value for key, value in root.items()
                        if key != "content_hash"}) != root.get("content_hash")):
        raise RuntimeError("OMS observation root hash differs")
    members = []
    seen = set()
    for row in rows.observations:
        key = (row.get("group_id"), row.get("broker_order_id"))
        if (set(row) != {name for name, _ in OBSERVATION.columns}
                or key in seen or not all(key)
                or any(row.get(field) != root.get(field) for field in (
                    "snapshot_id", "run_id", "snapshot_month",
                    "checkpoint_sequence"))
                or _digest({name: value for name, value in row.items()
                            if name != "content_hash"}) != row.get("content_hash")):
            raise RuntimeError("OMS observation child differs from its root")
        if (row["fingerprint_kind"] not in {"canonical", "live_adapter"}
                or (row["fingerprint_kind"] == "canonical"
                    and not row["lifecycle_state"])
                or (row["fingerprint_kind"] == "live_adapter"
                    and (row["lifecycle_state"] or row["rejection_code"]
                         or row["rejection_reason"]))):
            raise RuntimeError("OMS observation has invalid fingerprint kind")
        seen.add(key)
        members.append((*key, row["content_hash"]))
    if (len(rows.observations) != int(root["observation_count"])
            or _digest(tuple(sorted(members))) != root["observation_hash"]):
        raise RuntimeError("OMS observation set differs from its root")
    return rows


def load_unattested_oms_observation_snapshot(
    client: Any, *, run_id: str, checkpoint_sequence: int,
) -> OmsObservationSnapshotRows:
    """Read one exact root and its bounded children; this alone grants no resume."""
    from src.backend.backtest_market_data import assert_select_only
    from src.trading_runtime.arte_journal_writer import _literal, _rows

    if (type(run_id) is not str or not run_id
            or type(checkpoint_sequence) is not int or checkpoint_sequence < 1):
        raise ValueError("OMS observation read needs one checkpoint identity")
    predicate = (f"run_id={_literal(run_id)} "
                 f"AND checkpoint_sequence={checkpoint_sequence}")
    roots = _rows(client, assert_select_only(
        f"SELECT {','.join(name for name, _ in ROOT.columns)} "
        f"FROM arte.{ROOT.name} WHERE {predicate} "
        "LIMIT 2 FORMAT JSONEachRow"))
    if len(roots) != 1:
        raise RuntimeError("OMS observation has no unique checkpoint root")
    root = roots[0]
    count = int(root["observation_count"])
    if not 0 <= count <= 10_000:
        raise RuntimeError("OMS observation exceeds checkpoint memory budget")
    columns = ",".join(
        f"toString({name}) AS {name}" if "Decimal(" in kind else name
        for name, kind in OBSERVATION.columns)
    children = _rows(client, assert_select_only(
        f"SELECT {columns} FROM arte.{OBSERVATION.name} "
        f"WHERE {predicate} AND snapshot_id=toUUID("
        f"{_literal(str(UUID(str(root['snapshot_id']))))}) "
        f"ORDER BY group_id,broker_order_id LIMIT {count + 1} FORMAT JSONEachRow"))
    # ClickHouse 26.x toString(Decimal) omits trailing scale zeros. Restore
    # the sealed Decimal(38, 18) representation before hashing readback rows.
    decimal_fields = tuple(name for name, kind in OBSERVATION.columns
                           if "Decimal(" in kind)
    normalized = tuple({**row, **{
        field: _number(Decimal(str(row[field]))) for field in decimal_fields}}
                       for row in children)
    return verify_oms_observation_snapshot(
        OmsObservationSnapshotRows(root, normalized))


def load_attested_oms_observation_snapshot(
    client: Any, keeper: Any, *, run_id: str, checkpoint_sequence: int,
) -> OmsObservationSnapshotRows:
    """Join Keeper, V4 journal and completed market cursor before cold use."""
    from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
    from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor

    prefix = load_verified_v4_prefix(client, run_id)
    if (prefix is None or prefix.status != "running"
            or prefix.last_sequence != checkpoint_sequence
            or not callable(getattr(keeper, "read_head", None))):
        raise RuntimeError("OMS observation lacks a running verified V4 cursor")
    head = keeper.read_head(run_id=run_id)
    if (not isinstance(head, OmsObservationHead)
            or head.run_id != run_id
            or head.checkpoint_sequence != checkpoint_sequence
            or head.journal_batch_id != prefix.last_batch_id):
        raise RuntimeError("OMS observation Keeper head differs from V4 cursor")
    cursor = load_latest_backtest_cursor(client, prefix)
    if (not isinstance(cursor, dict) or cursor.get("run_id") != run_id
            or cursor.get("event_sequence") != checkpoint_sequence
            or cursor.get("batch_id") != prefix.last_batch_id):
        raise RuntimeError("OMS observation lacks a committed market cursor")
    rows = load_unattested_oms_observation_snapshot(
        client, run_id=run_id, checkpoint_sequence=checkpoint_sequence)
    if (rows.root["content_hash"] != head.snapshot_hash
            or rows.root["boundary_ms"] != cursor.get("boundary_ms")
            or rows.root["session_date"] != cursor.get("session_date")
            or keeper.read_head(run_id=run_id) != head):
        raise RuntimeError("OMS observation seal differs from selected cursor")
    return rows


def publish_oms_observation_snapshot(
    client: Any, session: ManagedKeeperSession,
    rows: OmsObservationSnapshotRows, *, journal_batch_id: str,
) -> OmsObservationHead:
    """Journal-worker-only children-first publication followed by Keeper CAS.

    A lost INSERT response leaves a pending dispatch operation; it cannot
    select a Keeper head or silently make an orphan root resumable.
    """
    from src.trading_runtime.arte_journal_commit_v4 import load_writer_v4_snapshot_prefix
    from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor
    from src.trading_runtime.arte_journal_writer import _insert
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch

    if (not isinstance(session, ManagedKeeperSession) or not session.writable
            or getattr(client, "typed_insert_strict", False) is not True
            or not isinstance(getattr(client, "typed_insert_dispatch", None),
                              TypedInsertDispatch)
            or client.typed_insert_dispatch.keeper is not session.client):
        raise RuntimeError("OMS observation publication lacks fenced writer")
    expected = verify_oms_observation_snapshot(rows)
    seal = expected.root
    run_id, sequence = seal["run_id"], seal["checkpoint_sequence"]
    try:
        if str(UUID(journal_batch_id)) != journal_batch_id:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("OMS observation batch ID is invalid") from exc
    prefix = load_writer_v4_snapshot_prefix(client, run_id)
    if (prefix is None or prefix.status != "running"
            or prefix.last_sequence != sequence
            or prefix.last_batch_id != journal_batch_id):
        raise RuntimeError("OMS observation lacks exact running V4 cursor")
    cursor = load_latest_backtest_cursor(client, prefix)
    if (not isinstance(cursor, dict) or cursor.get("run_id") != run_id
            or cursor.get("event_sequence") != sequence
            or cursor.get("batch_id") != journal_batch_id
            or cursor.get("boundary_ms") != seal["boundary_ms"]
            or cursor.get("session_date") != seal["session_date"]):
        raise RuntimeError("OMS observation cursor differs from captured state")
    reader = ManagedOmsObservationHeadReader(session)
    path = reader.path(run_id)
    previous = (None if session.client.exists(path) is None
                else reader.read_head(run_id=run_id))
    if previous is not None:
        if previous.checkpoint_sequence == sequence:
            if (previous.journal_batch_id != journal_batch_id
                    or previous.snapshot_hash != seal["content_hash"]
                    or load_attested_oms_observation_snapshot(
                        client, reader, run_id=run_id,
                        checkpoint_sequence=sequence) != expected):
                raise RuntimeError("OMS observation repeat differs from selected state")
            return previous
        if previous.checkpoint_sequence > sequence:
            raise RuntimeError("OMS observation would rewind Keeper head")
    operations: list[tuple[str, str]] = []
    for contract, family in ((OBSERVATION, expected.observations),
                             (ROOT, (seal,))):
        if not family:
            continue
        token = (f"oms-observation:{run_id}:{sequence}:"
                 f"{seal['content_hash']}:{contract.name}")
        _insert(client, contract.name, family, token,
                dispatch_sequence=sequence,
                dispatch_batch_id=journal_batch_id,
                dispatch_oms_observation_snapshot_hash=seal["content_hash"])
        operations.append((contract.name, token))
    observed = load_unattested_oms_observation_snapshot(
        client, run_id=run_id, checkpoint_sequence=sequence)
    if observed != expected:
        raise RuntimeError("OMS observation readback differs from captured state")
    for table, token in operations:
        client.typed_insert_dispatch.seal_verified_operation(
            run_id=run_id, table=table, token=token,
            batch_id=journal_batch_id, batch_last_sequence=sequence,
            oms_observation_snapshot=True)
    client.typed_insert_dispatch.compact_verified_oms_observation_snapshot(
        run_id=run_id, batch_id=journal_batch_id,
        last_sequence=sequence, snapshot_hash=seal["content_hash"],
        operations=tuple(operations), previous=previous)
    selected = reader.read_head(run_id=run_id)
    if (selected.checkpoint_sequence != sequence
            or selected.journal_batch_id != journal_batch_id
            or selected.snapshot_hash != seal["content_hash"]):
        raise RuntimeError("OMS observation Keeper readback differs")
    return selected
