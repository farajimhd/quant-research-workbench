"""Normalized, immutable Strategy 1 causal-evidence checkpoint contract.

Only the journal publisher may write these app-owned tables. Backtest market
readers never create or repair them. No JSON, arrays, or opaque state columns
are part of the storage contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, Mapping
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from src.backend.backtest_strategy_one_evidence import StrategyOneEvidenceState
from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.keeper_session import ManagedKeeperSession
from src.trading_runtime.strategy_one_activation_state import FrozenActivation
from src.trading_runtime.strategy_one_resistance import (
    KnownResistance, ResistanceObservation,
)


COMMON = (("snapshot_id", "UUID"), ("run_id", "String"),
          ("snapshot_month", "Date"), ("checkpoint_sequence", "UInt64"))
PARTITION = "toYYYYMM(snapshot_month)"
TABLES = (
    TableContract("trading_strategy_one_evidence_snapshot_v1",
                  COMMON + (("session_date", "Date"), ("boundary_ms", "UInt32"),
                            ("resistance_count", "UInt32"), ("known_count", "UInt32"),
                            ("activation_count", "UInt32"),
                            ("activation_level_count", "UInt32"),
                            ("low_count", "UInt32"),
                            ("content_hash", "FixedString(64)")),
                  PARTITION, "run_id, checkpoint_sequence, snapshot_id"),
    TableContract("trading_strategy_one_evidence_resistance_v1",
                  COMMON + (("ticker", "LowCardinality(String)"),
                            ("boundary_ms", "UInt32"), ("close_int", "UInt64")),
                  PARTITION, "run_id, checkpoint_sequence, ticker"),
    TableContract("trading_strategy_one_evidence_known_v1",
                  COMMON + (("ticker", "LowCardinality(String)"),
                            ("ordinal", "UInt16"), ("unified_level_id", "String"),
                            ("lower", "Decimal(38, 18)"),
                            ("upper", "Decimal(38, 18)"), ("accepted", "UInt8")),
                  PARTITION, "run_id, checkpoint_sequence, ticker, ordinal"),
    TableContract("trading_strategy_one_evidence_activation_v1",
                  COMMON + (("ticker", "LowCardinality(String)"),
                            ("boundary_ms", "UInt32"), ("price_int", "UInt64"),
                            ("average_gap", "Nullable(Decimal(38, 18))")),
                  PARTITION, "run_id, checkpoint_sequence, ticker, boundary_ms"),
    TableContract("trading_strategy_one_evidence_activation_level_v1",
                  COMMON + (("ticker", "LowCardinality(String)"),
                            ("boundary_ms", "UInt32"), ("ordinal", "UInt16"),
                            ("unified_level_id", "String")),
                  PARTITION,
                  "run_id, checkpoint_sequence, ticker, boundary_ms, ordinal"),
    TableContract("trading_strategy_one_evidence_30s_low_v1",
                  COMMON + (("ticker", "LowCardinality(String)"),
                            ("boundary_ms", "UInt32"), ("low_int", "UInt64")),
                  PARTITION, "run_id, checkpoint_sequence, ticker"),
)


@dataclass(frozen=True, slots=True)
class EvidenceSnapshotRows:
    snapshot: dict[str, Any]
    resistance: tuple[dict[str, Any], ...]
    known: tuple[dict[str, Any], ...]
    activations: tuple[dict[str, Any], ...]
    activation_levels: tuple[dict[str, Any], ...]
    lows: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class EvidenceSnapshotHead:
    run_id: str
    checkpoint_sequence: int
    journal_batch_id: str
    snapshot_hash: str
    keeper_version: int


class EvidenceSnapshotHeadReader(Protocol):
    def read_head(self, *, run_id: str) -> EvidenceSnapshotHead: ...


class ManagedEvidenceSnapshotHeadReader:
    """Read only a Keeper-selected, normalized evidence checkpoint."""

    def __init__(self, session: ManagedKeeperSession) -> None:
        if not isinstance(session, ManagedKeeperSession):
            raise TypeError("Strategy 1 evidence head requires managed Keeper")
        self._session = session

    @staticmethod
    def path(run_id: str) -> str:
        if (type(run_id) is not str or not run_id
                or any(char in run_id for char in "\r\n\x00")):
            raise ValueError("Strategy 1 evidence head run is invalid")
        return ("/trading/strategy-one-evidence-snapshot/v1/"
                + sha256(run_id.encode()).hexdigest() + "/head")

    def read_head(self, *, run_id: str) -> EvidenceSnapshotHead:
        session, client = self._session, self._session.client
        if not session.writable or client.client_id is None:
            raise RuntimeError("Strategy 1 evidence Keeper session is unavailable")
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
            head = EvidenceSnapshotHead(
                run_id, int(fields[2]), fields[3], fields[4], stat.version)
        except Exception as exc:
            raise ValueError("Strategy 1 evidence Keeper head missing or corrupt") from exc
        if (not session.writable or session._generation != generation
                or client.client_id != client_id):
            raise RuntimeError("Strategy 1 evidence Keeper session changed during read")
        return head


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode()).hexdigest()


def _decimal(value: object, *, positive: bool = True) -> str:
    if type(value) not in (int, float, str, Decimal):
        raise ValueError("Strategy 1 evidence decimal is not scalar")
    number = Decimal(str(value))
    exact = number.quantize(Decimal("0.000000000000000001"))
    if (not number.is_finite() or number != exact
            or positive and number <= 0 or len(exact.as_tuple().digits) > 38):
        raise ValueError("Strategy 1 evidence decimal exceeds fixed scale")
    return format(exact, "f")


def _canonical(rows: EvidenceSnapshotRows) -> EvidenceSnapshotRows:
    if not isinstance(rows, EvidenceSnapshotRows):
        raise TypeError("Strategy 1 evidence rows are not typed")
    groups = []
    for contract, items in zip(TABLES, (rows.snapshot, rows.resistance,
                                       rows.known, rows.activations,
                                       rows.activation_levels, rows.lows)):
        normalized = []
        for source in (items,) if isinstance(items, Mapping) else items:
            if not isinstance(source, Mapping) or set(source) != set(dict(contract.columns)):
                raise ValueError("Strategy 1 evidence row has missing or extra fields")
            row = dict(source)
            for name, kind in contract.columns:
                if "Decimal(" in kind and row[name] is not None:
                    row[name] = _decimal(row[name])
            normalized.append(row)
        groups.append(normalized[0] if isinstance(items, Mapping) else tuple(normalized))
    return EvidenceSnapshotRows(*groups)


def project_evidence_snapshot(*, run_id: str, session_date: date,
                              checkpoint_sequence: int,
                              state: StrategyOneEvidenceState) -> EvidenceSnapshotRows:
    if (str(UUID(run_id)) != run_id or not isinstance(session_date, date)
            or type(checkpoint_sequence) is not int or checkpoint_sequence < 1
            or not isinstance(state, StrategyOneEvidenceState)
            or type(state.boundary_ms) is not int
            or not 0 < state.boundary_ms <= 57_600_000
            or state.boundary_ms % 100):
        raise ValueError("Strategy 1 evidence needs one typed V4 checkpoint")
    snapshot_id = str(uuid5(NAMESPACE_URL,
        f"strategy-one-evidence-v1:{run_id}:{checkpoint_sequence}"))
    common = dict(snapshot_id=snapshot_id, run_id=run_id,
                  snapshot_month=session_date.replace(day=1).isoformat(),
                  checkpoint_sequence=checkpoint_sequence)
    resistance, known, activations, levels, lows = [], [], [], [], []
    seen_resistance = set()
    for ticker, observation in sorted(state.resistance):
        if (ticker in seen_resistance or not ticker or ticker != ticker.upper()
                or not isinstance(observation, ResistanceObservation)
                or not 0 <= observation.boundary_ms <= state.boundary_ms
                or observation.boundary_ms % 1_000
                or type(observation.close_int) is not int
                or observation.close_int < 0):
            raise ValueError("Strategy 1 resistance checkpoint is malformed")
        seen_resistance.add(ticker)
        resistance.append(dict(common, ticker=ticker,
                               boundary_ms=observation.boundary_ms,
                               close_int=observation.close_int))
        identities = {level.unified_level_id for level in observation.known}
        if (len(identities) != len(observation.known)
                or not observation.accepted_ids <= identities
                or len(observation.known) > 65_535):
            raise ValueError("Strategy 1 known resistance identities differ")
        for ordinal, level in enumerate(observation.known):
            if (not isinstance(level, KnownResistance) or not level.unified_level_id
                    or level.lower > level.upper):
                raise ValueError("Strategy 1 known resistance is untyped")
            known.append(dict(common, ticker=ticker, ordinal=ordinal,
                              unified_level_id=level.unified_level_id,
                              lower=_decimal(level.lower), upper=_decimal(level.upper),
                              accepted=int(level.unified_level_id in observation.accepted_ids)))
    seen_activation = set()
    for activation in sorted(state.activations, key=lambda row: (row.ticker, row.boundary_ms)):
        if (not isinstance(activation, FrozenActivation)
                or not activation.ticker or activation.ticker != activation.ticker.upper()
                or (activation.ticker, activation.boundary_ms) in seen_activation
                or not 0 < activation.boundary_ms <= state.boundary_ms
                or activation.boundary_ms % 100
                or type(activation.price_int) is not int or activation.price_int <= 0
                or len(activation.resistance_ids) > 65_535
                or len(set(activation.resistance_ids)) != len(activation.resistance_ids)):
            raise ValueError("Strategy 1 frozen activation is malformed")
        seen_activation.add((activation.ticker, activation.boundary_ms))
        activations.append(dict(common, ticker=activation.ticker,
                                boundary_ms=activation.boundary_ms,
                                price_int=activation.price_int,
                                average_gap=(_decimal(activation.average_gap)
                                             if activation.average_gap is not None else None)))
        for ordinal, identity in enumerate(activation.resistance_ids):
            if not identity:
                raise ValueError("Strategy 1 activation resistance identity is empty")
            levels.append(dict(common, ticker=activation.ticker,
                               boundary_ms=activation.boundary_ms, ordinal=ordinal,
                               unified_level_id=identity))
    seen_lows = set()
    for ticker, boundary, low_int in sorted(state.completed_30s_lows):
        if (ticker in seen_lows or not ticker or ticker != ticker.upper()
                or type(boundary) is not int or boundary % 30_000
                or not 0 <= state.boundary_ms - boundary < 30_000
                or type(low_int) is not int or low_int <= 0):
            raise ValueError("Strategy 1 completed 30s low is malformed")
        seen_lows.add(ticker)
        lows.append(dict(common, ticker=ticker, boundary_ms=boundary,
                         low_int=low_int))
    children = (tuple(resistance), tuple(known), tuple(activations),
                tuple(levels), tuple(lows))
    root = dict(common, session_date=session_date.isoformat(),
                boundary_ms=state.boundary_ms,
                resistance_count=len(resistance), known_count=len(known),
                activation_count=len(activations), activation_level_count=len(levels),
                low_count=len(lows), content_hash=_digest(children))
    return EvidenceSnapshotRows(root, *children)


def restore_evidence_snapshot(rows: EvidenceSnapshotRows) -> StrategyOneEvidenceState:
    rows = _canonical(rows)
    root = rows.snapshot
    if (any(row[key] != root[key] for group in (
            rows.resistance, rows.known, rows.activations,
            rows.activation_levels, rows.lows)
            for row in group for key in ("snapshot_id", "run_id", "snapshot_month",
                                      "checkpoint_sequence"))
            or tuple(len(group) for group in (
                rows.resistance, rows.known, rows.activations,
                rows.activation_levels, rows.lows)) != (
                    root["resistance_count"], root["known_count"],
                    root["activation_count"], root["activation_level_count"],
                    root["low_count"])
            or _digest((rows.resistance, rows.known, rows.activations,
                        rows.activation_levels, rows.lows)) != root["content_hash"]):
        raise RuntimeError("Strategy 1 evidence snapshot children differ from seal")
    by_known: dict[str, list[dict]] = {}
    for row in rows.known:
        by_known.setdefault(row["ticker"], []).append(row)
    resistance = []
    for row in rows.resistance:
        children = by_known.pop(row["ticker"], [])
        if [child["ordinal"] for child in children] != list(range(len(children))):
            raise RuntimeError("Strategy 1 known resistance order differs")
        known = tuple(KnownResistance(child["unified_level_id"],
                                      float(child["lower"]), float(child["upper"]))
                      for child in children)
        accepted = frozenset(child["unified_level_id"] for child in children
                             if child["accepted"] == 1)
        resistance.append((row["ticker"], ResistanceObservation(
            row["boundary_ms"], row["close_int"], known, accepted)))
    if by_known:
        raise RuntimeError("Strategy 1 known resistance lacks a parent")
    by_level: dict[tuple[str, int], list[dict]] = {}
    for row in rows.activation_levels:
        by_level.setdefault((row["ticker"], row["boundary_ms"]), []).append(row)
    activations = []
    for row in rows.activations:
        children = by_level.pop((row["ticker"], row["boundary_ms"]), [])
        if [child["ordinal"] for child in children] != list(range(len(children))):
            raise RuntimeError("Strategy 1 activation resistance order differs")
        activations.append(FrozenActivation(
            row["ticker"], row["boundary_ms"], row["price_int"],
            float(row["average_gap"]) if row["average_gap"] is not None else None,
            tuple(child["unified_level_id"] for child in children)))
    if by_level:
        raise RuntimeError("Strategy 1 activation resistance lacks a parent")
    state = StrategyOneEvidenceState(
        root["boundary_ms"], tuple(resistance), tuple(activations),
        tuple((row["ticker"], row["boundary_ms"], row["low_int"])
              for row in rows.lows))
    # Reproject to verify all semantic invariants, not only the child hash.
    if project_evidence_snapshot(
            run_id=root["run_id"], session_date=date.fromisoformat(root["session_date"]),
            checkpoint_sequence=root["checkpoint_sequence"], state=state) != rows:
        raise RuntimeError("Strategy 1 evidence snapshot is not canonical")
    return state


def load_unattested_evidence_snapshot_rows(
    client: Any, *, run_id: str, checkpoint_sequence: int,
) -> EvidenceSnapshotRows:
    """Read a complete historical image; caller proves V4/Keeper authority."""
    from src.backend.backtest_market_data import assert_select_only
    from src.trading_runtime.arte_journal_writer import _literal

    if (type(run_id) is not str or not run_id
            or type(checkpoint_sequence) is not int or checkpoint_sequence < 1
            or not callable(getattr(client, "execute", None))):
        raise ValueError("Strategy 1 evidence cold read needs exact run and cursor")

    def read(contract: TableContract, predicate: str, limit: int) -> tuple[dict, ...]:
        columns = ",".join(
            f"toString({name}) AS {name}" if "Decimal(" in kind else name
            for name, kind in contract.columns)
        sql = assert_select_only(
            f"SELECT {columns} FROM arte.{contract.name} WHERE {predicate} "
            f"ORDER BY {contract.order} LIMIT {limit} FORMAT JSONEachRow")
        return tuple(json.loads(line) for line in client.execute(sql).splitlines()
                     if line.strip())

    scope = (f"run_id={_literal(run_id)} "
             f"AND checkpoint_sequence={checkpoint_sequence}")
    roots = read(TABLES[0], scope, 2)
    if len(roots) != 1:
        raise RuntimeError("Strategy 1 evidence lacks exactly one snapshot root")
    root = roots[0]
    if (root.get("run_id") != run_id
            or root.get("checkpoint_sequence") != checkpoint_sequence):
        raise RuntimeError("Strategy 1 evidence root differs from requested cursor")
    snapshot_id = str(UUID(str(root["snapshot_id"])))
    predicate = f"snapshot_id=toUUID('{snapshot_id}')"
    counts = tuple(root[name] for name in (
        "resistance_count", "known_count", "activation_count",
        "activation_level_count", "low_count"))
    if any(type(count) is not int or not 0 <= count <= 100_000
           for count in counts):
        raise RuntimeError("Strategy 1 evidence child bound is invalid")
    rows = EvidenceSnapshotRows(
        root, *(read(contract, predicate, count + 1)
                for contract, count in zip(TABLES[1:], counts)))
    restore_evidence_snapshot(rows)
    return _canonical(rows)


def load_attested_evidence_snapshot(
    client: Any, keeper: EvidenceSnapshotHeadReader, *, run_id: str,
    checkpoint_sequence: int,
) -> StrategyOneEvidenceState:
    """Cold-read only Keeper-selected evidence at its committed V4 cursor."""
    from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
    from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor

    if (type(run_id) is not str or not run_id
            or type(checkpoint_sequence) is not int or checkpoint_sequence < 1
            or not callable(getattr(client, "execute", None))
            or not callable(getattr(keeper, "read_head", None))):
        raise ValueError("Strategy 1 evidence cold read lacks exact authorities")
    prefix = load_verified_v4_prefix(client, run_id)
    if (prefix is None or prefix.status != "running"
            or prefix.last_sequence != checkpoint_sequence
            or not prefix.batch_ids
            or prefix.last_batch_id != prefix.batch_ids[-1]):
        raise RuntimeError("Strategy 1 evidence lacks a running verified V4 cursor")
    first = keeper.read_head(run_id=run_id)
    if (not isinstance(first, EvidenceSnapshotHead)
            or first.run_id != run_id
            or first.checkpoint_sequence != checkpoint_sequence
            or first.journal_batch_id != prefix.last_batch_id
            or type(first.keeper_version) is not int or first.keeper_version < 0
            or type(first.snapshot_hash) is not str
            or len(first.snapshot_hash) != 64
            or any(char not in "0123456789abcdef" for char in first.snapshot_hash)):
        raise RuntimeError("Strategy 1 evidence Keeper head differs from V4 cursor")
    cursor = load_latest_backtest_cursor(client, prefix)
    if (not isinstance(cursor, dict)
            or cursor.get("run_id") != run_id
            or cursor.get("event_sequence") != checkpoint_sequence
            or cursor.get("batch_id") != prefix.last_batch_id):
        raise RuntimeError("Strategy 1 evidence lacks a committed market cursor")
    rows = load_unattested_evidence_snapshot_rows(
        client, run_id=run_id, checkpoint_sequence=checkpoint_sequence)
    if (rows.snapshot["content_hash"] != first.snapshot_hash
            or rows.snapshot["boundary_ms"] != cursor.get("boundary_ms")
            or rows.snapshot["session_date"] != cursor.get("session_date")):
        raise RuntimeError("Strategy 1 evidence seal differs from selected cursor")
    state = restore_evidence_snapshot(rows)
    if keeper.read_head(run_id=run_id) != first:
        raise RuntimeError("Strategy 1 evidence Keeper head changed during cold read")
    return state


def publish_evidence_snapshot(
    client: Any, session: ManagedKeeperSession,
    rows: EvidenceSnapshotRows, *, journal_batch_id: str,
) -> EvidenceSnapshotHead:
    """Journal-worker-only children-first, readback-first Keeper publication."""
    from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
    from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor
    from src.trading_runtime.arte_journal_writer import _insert
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch

    if (not isinstance(session, ManagedKeeperSession) or not session.writable
            or getattr(client, "typed_insert_strict", False) is not True
            or not isinstance(getattr(client, "typed_insert_dispatch", None),
                              TypedInsertDispatch)
            or client.typed_insert_dispatch.keeper is not session.client):
        raise RuntimeError("Strategy 1 evidence publication lacks fenced writer")
    rows = _canonical(rows)
    restored = restore_evidence_snapshot(rows)
    root = rows.snapshot
    run_id, sequence = root["run_id"], root["checkpoint_sequence"]
    if str(UUID(journal_batch_id)) != journal_batch_id:
        raise ValueError("Strategy 1 evidence batch ID is invalid")
    prefix = load_verified_v4_prefix(client, run_id)
    if (prefix is None or prefix.status != "running"
            or prefix.last_sequence != sequence
            or prefix.last_batch_id != journal_batch_id):
        raise RuntimeError("Strategy 1 evidence lacks exact running V4 cursor")
    cursor = load_latest_backtest_cursor(client, prefix)
    if (not isinstance(cursor, dict) or cursor.get("run_id") != run_id
            or cursor.get("event_sequence") != sequence
            or cursor.get("batch_id") != journal_batch_id
            or cursor.get("boundary_ms") != root["boundary_ms"]
            or cursor.get("session_date") != root["session_date"]):
        raise RuntimeError("Strategy 1 evidence cursor differs from capture")
    reader = ManagedEvidenceSnapshotHeadReader(session)
    path = reader.path(run_id)
    previous = (reader.read_head(run_id=run_id)
                if session.client.exists(path) is not None else None)
    if previous is not None:
        if previous.checkpoint_sequence == sequence:
            if (previous.journal_batch_id != journal_batch_id
                    or previous.snapshot_hash != root["content_hash"]
                    or restore_evidence_snapshot(load_unattested_evidence_snapshot_rows(
                        client, run_id=run_id,
                        checkpoint_sequence=sequence)) != restored):
                raise RuntimeError("Strategy 1 evidence repeat differs from selected state")
            return previous
        if previous.checkpoint_sequence > sequence:
            raise RuntimeError("Strategy 1 evidence would rewind Keeper head")
    families = tuple(zip(TABLES, (rows.snapshot, rows.resistance, rows.known,
                                  rows.activations, rows.activation_levels,
                                  rows.lows)))
    operations = []
    for contract, family in (*families[1:], families[0]):
        if not family:
            continue
        source = (family,) if isinstance(family, dict) else family
        token = (f"evidence-state:{run_id}:{sequence}:"
                 f"{root['content_hash']}:{contract.name}")
        _insert(client, contract.name, source, token,
                dispatch_sequence=sequence,
                dispatch_batch_id=journal_batch_id,
                dispatch_evidence_snapshot_hash=root["content_hash"])
        operations.append((contract.name, token))
    observed = load_unattested_evidence_snapshot_rows(
        client, run_id=run_id, checkpoint_sequence=sequence)
    if observed != rows:
        raise RuntimeError("Strategy 1 evidence readback differs from capture")
    for table, token in operations:
        client.typed_insert_dispatch.seal_verified_operation(
            run_id=run_id, table=table, token=token,
            batch_id=journal_batch_id, batch_last_sequence=sequence,
            evidence_snapshot=True)
    client.typed_insert_dispatch.compact_verified_evidence_snapshot(
        run_id=run_id, batch_id=journal_batch_id, last_sequence=sequence,
        snapshot_hash=root["content_hash"],
        operations=tuple(operations), previous=previous)
    selected = reader.read_head(run_id=run_id)
    if (selected.checkpoint_sequence != sequence
            or selected.journal_batch_id != journal_batch_id
            or selected.snapshot_hash != root["content_hash"]):
        raise RuntimeError("Strategy 1 evidence Keeper readback differs")
    return selected
