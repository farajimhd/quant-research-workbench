"""Shared, bit-exact OMS observations for future strategy checkpoints.

These inert contracts do not install tables or grant restart admission. The
legacy codec supplies fingerprint shape validation and pure actor restoration;
The unchanged snapshot-root relation is reused; typed detail rows and the
Keeper namespace remain separately scoped. Quantity bits here are
OMS deduplication observations, not a replacement for financial quantity ledgers.
Zero follows the existing shared scalar codec's canonical positive-zero rule.
"""
from dataclasses import dataclass, replace
from types import MappingProxyType, SimpleNamespace
from collections.abc import Mapping
from datetime import date
from uuid import NAMESPACE_URL, uuid5
from hashlib import sha256
import json
import re

from . import strategy_one_oms_observation_snapshot as codec
from .trading_price_codec import encode_trading_price, decode_trading_price

_NUMBERS = ("filled_quantity", "remaining_quantity", "average_fill_price",
            "limit_price", "stop_price")
ROOT = replace(codec.ROOT, name="trading_strategy_one_oms_observation_snapshot_v1")
OBSERVATION = replace(codec.OBSERVATION, name="trading_oms_observation_v1",
    columns=tuple((name + "_bits", "UInt64") if name in _NUMBERS else (name, kind)
                  for name, kind in codec.OBSERVATION.columns))
TABLES = (ROOT, OBSERVATION)


class ManagedSharedOmsObservationHeadReader(codec.ManagedOmsObservationHeadReader):
    """Existing managed scalar head decoder in the central OMS namespace."""
    @staticmethod
    def path(run_id):
        codec.ManagedOmsObservationHeadReader.path(run_id)
        return "/trading/oms-observation/v1/" + sha256(run_id.encode()).hexdigest() + "/head"


@dataclass(frozen=True, slots=True)
class SharedOmsObservationSnapshot:
    root: Mapping
    observations: tuple[Mapping, ...]

    def __post_init__(self):
        object.__setattr__(self, "root", MappingProxyType(dict(self.root)))
        object.__setattr__(self, "observations",
            tuple(MappingProxyType(dict(row)) for row in self.observations))


def _root(root, children):
    fields = {key: value for key, value in root.items() if key != "content_hash"}
    fields["observation_count"] = len(children)
    fields["observation_hash"] = codec._digest(tuple(sorted(
        (row["group_id"], row["broker_order_id"], row["content_hash"])
        for row in children)))
    return codec._sealed(fields)


def project_shared_oms_observations(*, run_id, session_date, checkpoint_sequence,
        boundary_ms, groups):
    """Freeze last observed fingerprints, independently of broker-now state."""
    if type(session_date) is not date or not isinstance(groups, Mapping):
        raise ValueError("Shared OMS observations need a date and native groups")
    originals, shaped = {}, {}
    for identity, group in groups.items():
        observed = dict(group.broker_order_state_fingerprints)
        placeholders = {}
        for broker_id, fingerprint in observed.items():
            if type(fingerprint) is not tuple or len(fingerprint) not in (7, 10):
                raise ValueError("Shared OMS observation has an invalid fingerprint")
            start = 2 if len(fingerprint) == 10 else 1
            originals[(identity, broker_id)] = tuple(encode_trading_price(value,
                allow_zero=True) for value in fingerprint[start:start + 5])
            placeholders[broker_id] = (*fingerprint[:start], *(0.0,) * 5,
                                       *fingerprint[start + 5:])
        shaped[identity] = SimpleNamespace(broker_order_ids=tuple(group.broker_order_ids),
            broker_order_state_fingerprints=placeholders)
    validated = codec.verify_oms_observation_snapshot(codec.project_oms_observation_snapshot(
        run_id=run_id, session_date=session_date, checkpoint_sequence=checkpoint_sequence,
        boundary_ms=boundary_ms, groups=shaped))
    snapshot_id = str(uuid5(NAMESPACE_URL,
        f"{run_id}:{checkpoint_sequence}:shared-oms-observation-v1"))
    children = []
    for row in validated.observations:
        fields = {key: value for key, value in row.items()
                  if key not in (*_NUMBERS, "content_hash")}
        fields["snapshot_id"] = snapshot_id
        fields.update(zip((name + "_bits" for name in _NUMBERS),
            originals[(row["group_id"], row["broker_order_id"])], strict=True))
        children.append(codec._sealed(fields))
    root = _root({**validated.root, "snapshot_id": snapshot_id}, children)
    return verify_shared_oms_observations(SharedOmsObservationSnapshot(root, tuple(children)))


def verified_oms_observation_codec_rows(rows):
    """Adapt verified scalars for existing pure restoration, without rounding."""
    verified = verify_shared_oms_observations(rows)
    return _codec_rows(verified)


def _codec_rows(rows):
    children = []
    for row in rows.observations:
        fields = {key: value for key, value in row.items()
                  if key not in (*(name + "_bits" for name in _NUMBERS), "content_hash")}
        fields.update({name: repr(decode_trading_price(row[name + "_bits"], allow_zero=True))
                       for name in _NUMBERS})
        children.append(codec._sealed(fields))
    return codec.OmsObservationSnapshotRows(_root(rows.root, children), tuple(children))


def verify_shared_oms_observations(rows):
    if type(rows) is not SharedOmsObservationSnapshot:
        raise TypeError("Shared OMS observations require central typed rows")
    root = rows.root
    if (set(root) != {name for name, _ in ROOT.columns}
            or type(root["checkpoint_sequence"]) is not int
            or not 1 <= root["checkpoint_sequence"] < 2 ** 64
            or type(root["boundary_ms"]) is not int
            or not 0 <= root["boundary_ms"] <= 57_600_000
            or root["boundary_ms"] % 100
            or type(root["observation_count"]) is not int
            or not 0 <= root["observation_count"] < 2 ** 32
            or type(root["run_id"]) is not str or not root["run_id"]
            or any(char in root["run_id"] for char in "\r\n\x00")):
        raise ValueError("Shared OMS observation root differs from its scalar contract")
    day = date.fromisoformat(root["session_date"])
    if (root["snapshot_month"] != day.replace(day=1).isoformat()
            or root["snapshot_id"] != str(uuid5(NAMESPACE_URL,
                f"{root['run_id']}:{root['checkpoint_sequence']}:shared-oms-observation-v1"))
            or _root(root, rows.observations) != dict(root)):
        raise ValueError("Shared OMS observation root identity or seal differs")
    for row in rows.observations:
        if (set(row) != {name for name, _ in OBSERVATION.columns}
                or codec._sealed({key: value for key, value in row.items()
                                 if key != "content_hash"}) != dict(row)
                or any(type(row[name]) is not str for name, kind in OBSERVATION.columns
                       if kind in {"String", "LowCardinality(String)"})):
            raise ValueError("Shared OMS observation child scalar or seal differs")
    codec.verify_oms_observation_snapshot(_codec_rows(rows))
    return rows


def read_shared_oms_observations(client, *, run_id, checkpoint_sequence,
        expected_content_hash, maximum_rows=100000, maximum_bytes=16 * 1024 * 1024):
    """Read one pinned central image; SQL rows alone grant no restart admission."""
    from src.backend.backtest_market_data import _literal, assert_select_only
    from .arte_journal_schema import storage_preflight
    if (type(run_id) is not str or not run_id
            or any(char in run_id for char in "\r\n\x00")
            or type(checkpoint_sequence) is not int or not 1 <= checkpoint_sequence < 2 ** 64
            or type(expected_content_hash) is not str
            or re.fullmatch(r"[0-9a-f]{64}", expected_content_hash) is None
            or type(maximum_rows) is not int or maximum_rows < 1
            or type(maximum_bytes) is not int or maximum_bytes < 1):
        raise ValueError("Shared OMS read requires bounded pinned scope")
    storage_preflight(client, tables=TABLES)
    used = 0
    predicate = f"run_id={_literal(run_id)} AND checkpoint_sequence={checkpoint_sequence}"
    def pairs(items):
        fields = {}
        for key, value in items:
            if key in fields:
                raise ValueError("Shared OMS transport repeated a field")
            fields[key] = value
        return fields
    def read(table, limit, *, snapshot_id=None):
        nonlocal used
        where = predicate
        if snapshot_id is not None:
            where += f" AND snapshot_id=toUUID({_literal(snapshot_id)})"
        response = client.execute(assert_select_only(
            f"SELECT {','.join(name for name, _ in table.columns)} "
            f"FROM arte.{table.name} WHERE {where} ORDER BY {table.order} "
            f"LIMIT {limit} FORMAT JSONEachRow"))
        if type(response) is not str:
            raise ValueError("Shared OMS transport requires bounded text")
        used += len(response.encode())
        if used > maximum_bytes:
            raise ValueError("Shared OMS response exceeds byte budget")
        rows = tuple(json.loads(line, object_pairs_hook=pairs)
                     for line in response.splitlines() if line.strip())
        if len(rows) > limit:
            raise ValueError("Shared OMS transport exceeds requested row bound")
        for row in rows:
            if type(row) is not dict:
                raise ValueError("Shared OMS transport requires named fields")
            for name, kind in table.columns:
                if kind.startswith("UInt") and type(row.get(name)) is str:
                    if re.fullmatch(r"0|[1-9][0-9]*", row[name]) is None:
                        raise ValueError("Shared OMS UInt transport is not canonical")
                    row[name] = int(row[name])
        return rows
    roots = read(ROOT, 2)
    if len(roots) != 1:
        raise ValueError("Shared OMS root is missing or duplicate")
    root = roots[0]
    count = root.get("observation_count")
    if (type(count) is not int or not 0 <= count <= maximum_rows
            or root.get("run_id") != run_id
            or root.get("checkpoint_sequence") != checkpoint_sequence
            or root.get("content_hash") != expected_content_hash):
        raise ValueError("Shared OMS root differs from pinned scope or row budget")
    children = read(OBSERVATION, count + 1, snapshot_id=root["snapshot_id"])
    if len(children) != count:
        raise ValueError("Shared OMS children are missing or duplicate")
    result = verify_shared_oms_observations(SharedOmsObservationSnapshot(root, children))
    if read(ROOT, 2) != roots:
        raise ValueError("Shared OMS root changed during readback")
    return result


async def load_shared_oms_observations(client_factory, **scope):
    """Own dependent checkpoint SELECTs off-loop and drain cancellation."""
    from src.backend.qmd_columnar_history_transport import _owned_worker
    if not callable(client_factory):
        raise ValueError("Shared OMS loader requires an owned reader factory")
    def load():
        client = client_factory()
        try:
            return read_shared_oms_observations(client, **scope)
        finally:
            client.close()
    return await _owned_worker(load)


def read_attested_shared_oms_observations(client, head_reader, *, prefix,
        maximum_rows=100000, maximum_bytes=16 * 1024 * 1024):
    """Prove central selection at an unchanged running V4 market boundary.

    This proves only the observation checkpoint. Full programme restart also
    requires matching broker, financial, policy and source reconstruction.
    """
    from .arte_journal_commit_v4 import V4CommittedPrefix, load_verified_v4_prefix
    from .arte_journal_projection import load_latest_backtest_cursor
    if (type(prefix) is not V4CommittedPrefix or prefix.status != "running"
            or type(head_reader) is not ManagedSharedOmsObservationHeadReader):
        raise ValueError("Shared OMS attestation needs its running prefix and managed central head")
    session = head_reader._session
    keeper, generation, client_id = session.client, session._generation, session.client.client_id
    head = head_reader.read_head(run_id=prefix.run_id)
    if (head.checkpoint_sequence != prefix.last_sequence
            or head.journal_batch_id != prefix.last_batch_id
            or load_verified_v4_prefix(client, prefix.run_id) != prefix):
        raise ValueError("Shared OMS head differs from its running V4 cursor")
    rows = read_shared_oms_observations(client, run_id=prefix.run_id,
        checkpoint_sequence=prefix.last_sequence, expected_content_hash=head.snapshot_hash,
        maximum_rows=maximum_rows, maximum_bytes=maximum_bytes)
    cursor = load_latest_backtest_cursor(client, prefix)
    if (not isinstance(cursor, Mapping) or cursor.get("run_id") != prefix.run_id
            or cursor.get("event_sequence") != head.checkpoint_sequence
            or cursor.get("batch_id") != head.journal_batch_id
            or cursor.get("boundary_ms") != rows.root["boundary_ms"]
            or cursor.get("session_date") != rows.root["session_date"]):
        raise ValueError("Shared OMS observation differs from its completed market cursor")
    if (load_verified_v4_prefix(client, prefix.run_id) != prefix
            or head_reader.read_head(run_id=prefix.run_id) != head):
        raise ValueError("Shared OMS selection moved during attested readback")
    if (head_reader._session is not session or session.client is not keeper
            or not session.writable or session._generation != generation
            or keeper.client_id != client_id):
        raise RuntimeError("Shared OMS Keeper session changed during attested readback")
    return rows


async def load_attested_shared_oms_observations(client_factory, head_reader, **scope):
    """Own attestation off-loop, retaining the client until cancellation settles."""
    from src.backend.qmd_columnar_history_transport import _owned_worker
    if not callable(client_factory):
        raise ValueError("Shared OMS attestation requires an owned reader factory")
    def load():
        client = client_factory()
        try:
            return read_attested_shared_oms_observations(client, head_reader, **scope)
        finally:
            client.close()
    return await _owned_worker(load)


def publish_shared_oms_observations(client, session, rows, *, journal_batch_id):
    """Journal-worker-only children-first publication and central Keeper CAS."""
    from .keeper_session import ManagedKeeperSession
    from .arte_typed_insert_dispatch import TypedInsertDispatch
    from .arte_journal_commit_v4 import load_writer_v4_snapshot_prefix
    from .arte_journal_projection import load_latest_backtest_cursor
    from .arte_journal_schema import storage_preflight
    from .arte_journal_writer import _insert
    from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
    from uuid import UUID
    if (type(session) is not ManagedKeeperSession or not session.writable
            or getattr(client, "typed_insert_strict", False) is not True
            or type(getattr(client, "typed_insert_dispatch", None)) is not TypedInsertDispatch
            or client.typed_insert_dispatch.keeper is not session.client):
        raise RuntimeError("Shared OMS publication requires its strict owned writer")
    expected = verify_shared_oms_observations(rows)
    root = expected.root
    run_id, sequence = root["run_id"], root["checkpoint_sequence"]
    if type(journal_batch_id) is not str or str(UUID(journal_batch_id)) != journal_batch_id:
        raise ValueError("Shared OMS publication batch ID is not canonical")
    lease = getattr(client, "backtest_v4_lease", None)
    if type(lease) is not BacktestV4KeeperLease or lease.run_id != run_id:
        raise RuntimeError("Shared OMS publication lacks its current run lease")
    keeper, generation, client_id = session.client, session._generation, session.client.client_id
    def require_owner():
        if (not session.writable or session.client is not keeper
                or session._generation != generation or keeper.client_id != client_id
                or getattr(client, "backtest_v4_lease", None) is not lease):
            raise RuntimeError("Shared OMS publication changed Keeper ownership")
        lease.assert_current()
    require_owner()
    storage_preflight(client, tables=TABLES)
    prefix = load_writer_v4_snapshot_prefix(client, run_id)
    if (prefix is None or prefix.status != "running" or prefix.last_sequence != sequence
            or prefix.last_batch_id != journal_batch_id):
        raise RuntimeError("Shared OMS publication lacks its exact running V4 cursor")
    cursor = load_latest_backtest_cursor(client, prefix)
    if (not isinstance(cursor, Mapping) or cursor.get("run_id") != run_id
            or cursor.get("event_sequence") != sequence or cursor.get("batch_id") != journal_batch_id
            or cursor.get("boundary_ms") != root["boundary_ms"]
            or cursor.get("session_date") != root["session_date"]):
        raise RuntimeError("Shared OMS publication differs from its completed market cursor")
    reader = ManagedSharedOmsObservationHeadReader(session)
    previous = None if keeper.exists(reader.path(run_id)) is None else reader.read_head(run_id=run_id)
    if previous is not None:
        if previous.checkpoint_sequence > sequence:
            raise RuntimeError("Shared OMS publication would rewind its central head")
        if previous.checkpoint_sequence == sequence:
            if read_attested_shared_oms_observations(client, reader, prefix=prefix) != expected:
                raise RuntimeError("Shared OMS repeated publication differs from selected state")
            require_owner()
            return previous
    operations = []
    for table, family in ((OBSERVATION, expected.observations), (ROOT, (root,))):
        if not family:
            continue
        require_owner()
        token = f"oms-observation:{run_id}:{sequence}:{root['content_hash']}:{table.name}"
        _insert(client, table.name, family, token, dispatch_sequence=sequence,
            dispatch_batch_id=journal_batch_id, dispatch_oms_observation_snapshot_hash=root["content_hash"])
        operations.append((table.name, token))
    observed = read_shared_oms_observations(client, run_id=run_id,
        checkpoint_sequence=sequence, expected_content_hash=root["content_hash"])
    if observed != expected:
        raise RuntimeError("Shared OMS publication readback differs from captured state")
    require_owner()
    for table, token in operations:
        client.typed_insert_dispatch.seal_verified_operation(run_id=run_id, table=table,
            token=token, batch_id=journal_batch_id, batch_last_sequence=sequence,
            oms_observation_snapshot=True)
    require_owner()
    client.typed_insert_dispatch.compact_verified_shared_oms_observation_snapshot(
        run_id=run_id, batch_id=journal_batch_id, last_sequence=sequence,
        snapshot_hash=root["content_hash"], operations=tuple(operations), previous=previous)
    selected = reader.read_head(run_id=run_id)
    require_owner()
    if (selected.checkpoint_sequence != sequence or selected.journal_batch_id != journal_batch_id
            or selected.snapshot_hash != root["content_hash"]):
        raise RuntimeError("Shared OMS publication central head differs from captured state")
    return selected
