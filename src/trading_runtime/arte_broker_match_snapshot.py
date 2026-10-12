"""Central simulator snapshot contracts using the existing bit-exact codec.

These staged families are shared by future strategies. They install nothing.
Codec UUIDs retain their original versioned namespace; no legacy table is read
or written by this module. A cold row read is not a Keeper/V4 restart admission.
"""
from dataclasses import dataclass, replace
from types import MappingProxyType
from collections.abc import Mapping
import json
import re
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from . import strategy_one_broker_match_snapshot as codec
from .backtest_broker_contract import BacktestBrokerContract
from .arte_journal_schema import storage_preflight

_NAMES = ("snapshot", "account", "position", "open_order", "ticker", "performance_mark")
ROOT, ACCOUNT, POSITION, OPEN_ORDER, TICKER, MARK = tuple(replace(table,
    name=f"trading_simulated_broker_match_{name}_v1") for table, name in zip(codec.TABLES, _NAMES, strict=True))
ROOT = replace(ROOT, columns=tuple(("codec_content_hash", kind) if name == "content_hash" else (name, kind)
    for name, kind in ROOT.columns) + (("broker_contract_hash", "FixedString(64)"),
        ("liquidity_resolution_ms", "UInt32"), ("content_hash", "FixedString(64)")))
TABLES = (ROOT, ACCOUNT, POSITION, OPEN_ORDER, TICKER, MARK)


class ManagedSharedBrokerMatchHeadReader(codec.ManagedBrokerMatchHeadReader):
    """Reuse the managed head decoder with a central run-owned namespace."""
    @staticmethod
    def path(run_id):
        # Retain the existing exact run-ID validation without touching its head.
        codec.ManagedBrokerMatchHeadReader.path(run_id)
        return "/trading/simulated-broker-match/v1/" + sha256(run_id.encode()).hexdigest() + "/head"


@dataclass(frozen=True)
class SharedBrokerMatchSnapshot:
    snapshot: Mapping
    accounts: tuple
    positions: tuple
    open_orders: tuple
    tickers: tuple
    marks: tuple

    def __post_init__(self):
        object.__setattr__(self, "snapshot", MappingProxyType(dict(self.snapshot)))
        for name in ("accounts", "positions", "open_orders", "tickers", "marks"):
            object.__setattr__(self, name, tuple(MappingProxyType(dict(row)) for row in getattr(self, name)))


def _scope(contract, run_id):
    if type(contract) is not BacktestBrokerContract or contract.row["run_id"] != run_id:
        raise ValueError("Shared broker snapshot requires its immutable run contract")
    return contract.row["content_hash"], contract.config.liquidity_resolution_ms


def _row(table, values):
    row = codec._canonical_row(table, values)
    for name, kind in table.columns:
        unsigned = re.fullmatch(r"(?:Nullable\()?UInt([0-9]+)\)?", kind)
        if unsigned and row[name] is not None and row[name] >= 1 << int(unsigned.group(1)):
            raise ValueError("Shared broker integer exceeds its declared column width")
    return row


def _codec_rows(rows):
    root = dict(rows.snapshot)
    root["content_hash"] = root.pop("codec_content_hash")
    root.pop("broker_contract_hash")
    root.pop("liquidity_resolution_ms")
    return codec.BrokerMatchSnapshotRows(root, rows.accounts, rows.positions,
        rows.open_orders, rows.tickers, rows.marks)


def verify_shared_broker_snapshot(rows, *, broker_contract, run_id, checkpoint_sequence,
        expected_content_hash):
    """Verify both the codec seal and declared broker/resolution binding."""
    if type(rows) is not SharedBrokerMatchSnapshot or type(checkpoint_sequence) is not int or checkpoint_sequence < 1:
        raise ValueError("Shared broker verification needs central typed rows")
    contract_hash, resolution = _scope(broker_contract, run_id)
    root = _row(ROOT, rows.snapshot)
    if (root["run_id"] != run_id or root["checkpoint_sequence"] != checkpoint_sequence
            or root["broker_contract_hash"] != contract_hash or root["liquidity_resolution_ms"] != resolution
            or root["boundary_ms"] % resolution or root["content_hash"] != expected_content_hash
            or root["content_hash"] != codec._digest({key: value for key, value in root.items() if key != "content_hash"})):
        raise ValueError("Shared broker snapshot differs from its pinned scope or hash")
    families = tuple(tuple(_row(table, row) for row in family) for table, family in zip(TABLES[1:],
        (rows.accounts, rows.positions, rows.open_orders, rows.tickers, rows.marks), strict=True))
    canonical = codec.verify_broker_match_snapshot(_codec_rows(SharedBrokerMatchSnapshot(root, *families)))
    if any(row["last_boundary_ms"] % resolution for row in canonical.tickers):
        raise ValueError("Shared broker ticker clock differs from declared liquidity")
    return SharedBrokerMatchSnapshot(root, canonical.accounts, canonical.positions,
        canonical.open_orders, canonical.tickers, canonical.marks)


def project_shared_broker_snapshot(*, run_id, session_date, checkpoint_sequence, boundary_ms,
        state, simulation_config, broker_contract):
    contract_hash, resolution = _scope(broker_contract, run_id)
    broker_contract.require_config(simulation_config, run_id=run_id, content_hash=contract_hash,
        liquidity_plan_hash=broker_contract.row["liquidity_plan_hash"])
    rows = codec.project_broker_match_snapshot(run_id=run_id, session_date=session_date,
        checkpoint_sequence=checkpoint_sequence, boundary_ms=boundary_ms, state=state)
    root = dict(rows.snapshot)
    root["codec_content_hash"] = root.pop("content_hash")
    root.update(broker_contract_hash=contract_hash, liquidity_resolution_ms=resolution)
    root["content_hash"] = codec._digest(root)
    result = SharedBrokerMatchSnapshot(root, rows.accounts, rows.positions, rows.open_orders, rows.tickers, rows.marks)
    return verify_shared_broker_snapshot(result, broker_contract=broker_contract, run_id=run_id,
        checkpoint_sequence=checkpoint_sequence, expected_content_hash=root["content_hash"])


def read_shared_broker_snapshot(client, *, run_id, checkpoint_sequence, broker_contract,
        expected_content_hash, maximum_rows=100000, maximum_bytes=16 * 1024 * 1024,
        child_client_factory=None, concurrency=None):
    """Bounded central SELECTs and SSD placement checks; no legacy fallback."""
    from src.backend.backtest_market_data import _literal, assert_select_only
    _scope(broker_contract, run_id)
    if (type(checkpoint_sequence) is not int or checkpoint_sequence < 1
            or type(maximum_rows) is not int or maximum_rows < 1
            or type(maximum_bytes) is not int or maximum_bytes < 1
            or child_client_factory is not None and (not callable(child_client_factory)
                or type(concurrency) is not int or not 1 <= concurrency <= 8)
            or type(expected_content_hash) is not str or re.fullmatch(r"[0-9a-f]{64}", expected_content_hash) is None):
        raise ValueError("Shared broker read requires bounded pinned scope")
    storage_preflight(client, tables=TABLES)
    used = 0
    budget_lock = Lock()
    connection_lock, child_connections = Lock(), []
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Shared broker transport repeated a field")
            result[key] = value
        return result
    def read(table, limit, snapshot_id=None, reader=None):
        nonlocal used
        where = f"run_id={_literal(run_id)} AND checkpoint_sequence={checkpoint_sequence}"
        if snapshot_id is not None:
            where += f" AND snapshot_id=toUUID({_literal(snapshot_id)})"
        text = (client if reader is None else reader).execute(assert_select_only(f"SELECT {','.join(name for name, _ in table.columns)} "
            f"FROM arte.{table.name} WHERE {where} ORDER BY {table.order} LIMIT {limit} FORMAT JSONEachRow"))
        if type(text) is not str:
            raise ValueError("Shared broker transport must return bounded text")
        with budget_lock:
            used += len(text.encode())
            if used > maximum_bytes:
                raise ValueError("Shared broker read exceeded its response-byte budget")
        rows = [json.loads(line, object_pairs_hook=pairs) for line in text.splitlines() if line.strip()]
        for row in rows:
            for name, kind in table.columns:
                if kind.startswith(("UInt", "Nullable(UInt")) and isinstance(row.get(name), str):
                    if re.fullmatch(r"0|[1-9][0-9]*", row[name]) is None:
                        raise ValueError("Shared broker UInt transport is not canonical")
                    row[name] = int(row[name])
        return tuple(rows)
    roots = read(ROOT, 2)
    if len(roots) != 1:
        raise ValueError("Shared broker root is missing or duplicate")
    root = _row(ROOT, roots[0])
    requests, total = [], 0
    for table, family in zip(TABLES[1:], ("account", "position", "open_order", "ticker", "mark"), strict=True):
        count = root[f"{family}_count"]
        total += count
        if total > maximum_rows:
            raise ValueError("Shared broker read exceeded its child-row budget")
        requests.append((table, count))
    def child(request):
        table, count = request
        if child_client_factory is None:
            rows = read(table, count + 1, root["snapshot_id"])
        else:
            reader = child_client_factory()
            with connection_lock:
                if reader is client or any(reader is prior for prior in child_connections):
                    raise ValueError("Shared broker child read cannot reuse an owned connection")
                child_connections.append(reader)
            try:
                rows = read(table, count + 1, root["snapshot_id"], reader=reader)
            finally:
                reader.close()
        if len(rows) != count:
            raise ValueError("Shared broker children are missing or duplicate")
        return rows
    if child_client_factory is None:
        children = tuple(child(request) for request in requests)
    else:
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="shared-broker-read") as pool:
            futures = [pool.submit(child, request) for request in requests]
            children, errors = [], []
            for future in futures:
                try:
                    children.append(future.result())
                except BaseException as failure:
                    errors.append(failure)
            if len(errors) == 1:
                raise errors[0]
            if errors:
                raise BaseExceptionGroup("Shared broker child reads failed", errors)
    result = verify_shared_broker_snapshot(SharedBrokerMatchSnapshot(root, *children),
        broker_contract=broker_contract, run_id=run_id, checkpoint_sequence=checkpoint_sequence,
        expected_content_hash=expected_content_hash)
    if read(ROOT, 2) != roots:
        raise ValueError("Shared broker root changed during readback")
    return result


async def load_shared_broker_snapshot(client_factory, *, concurrency, **scope):
    """Own concurrent child connections off-loop and settle cancellation."""
    from src.backend.qmd_columnar_history_transport import _owned_worker
    if not callable(client_factory) or type(concurrency) is not int or not 1 <= concurrency <= 8:
        raise ValueError("Shared broker loader requires bounded owned reader concurrency")
    def load():
        client = client_factory()
        try:
            return read_shared_broker_snapshot(client, **scope,
                child_client_factory=client_factory, concurrency=concurrency)
        finally:
            client.close()
    return await _owned_worker(load)


def read_attested_shared_broker_snapshot(client, head_reader, *, prefix, broker_contract,
        child_client_factory=None, concurrency=None, maximum_rows=100000, maximum_bytes=16 * 1024 * 1024):
    """Require one unchanged running V4 cursor and central Keeper selection.

    This attests snapshot selection. Programme/source/OMS recovery and later
    writer ownership remain separate admission requirements.
    """
    from .arte_journal_commit_v4 import V4CommittedPrefix, load_verified_v4_prefix
    from .arte_journal_projection import load_latest_backtest_cursor
    if (type(prefix) is not V4CommittedPrefix or prefix.status != "running"
            or type(head_reader) is not ManagedSharedBrokerMatchHeadReader):
        raise ValueError("Central broker attestation requires its typed running prefix and managed head")
    session = head_reader._session
    keeper_client, generation, client_id = session.client, session._generation, session.client.client_id
    head = head_reader.read_head(run_id=prefix.run_id)
    if (head.checkpoint_sequence != prefix.last_sequence or head.journal_batch_id != prefix.last_batch_id
            or load_verified_v4_prefix(client, prefix.run_id) != prefix):
        raise ValueError("Central broker head differs from its running V4 receipt")
    rows = read_shared_broker_snapshot(client, run_id=prefix.run_id, checkpoint_sequence=prefix.last_sequence,
        broker_contract=broker_contract, expected_content_hash=head.snapshot_hash,
        maximum_rows=maximum_rows, maximum_bytes=maximum_bytes,
        child_client_factory=child_client_factory, concurrency=concurrency)
    cursor = load_latest_backtest_cursor(client, prefix)
    if (not isinstance(cursor, Mapping) or cursor.get("run_id") != prefix.run_id
            or cursor.get("event_sequence") != head.checkpoint_sequence
            or cursor.get("batch_id") != head.journal_batch_id
            or cursor.get("boundary_ms") != rows.snapshot["boundary_ms"]
            or cursor.get("session_date") != rows.snapshot["session_date"]):
        raise ValueError("Central broker snapshot differs from its completed market cursor")
    if (load_verified_v4_prefix(client, prefix.run_id) != prefix
            or head_reader.read_head(run_id=prefix.run_id) != head):
        raise ValueError("Central broker selection moved during attested readback")
    if (head_reader._session is not session or session.client is not keeper_client
            or not session.writable or session._generation != generation or keeper_client.client_id != client_id):
        raise RuntimeError("Central broker Keeper session changed during attested readback")
    return rows


async def load_attested_shared_broker_snapshot(client_factory, head_reader, *, concurrency, **scope):
    """Off-loop attestation with concurrent owned child reads and safe close."""
    from src.backend.qmd_columnar_history_transport import _owned_worker
    if not callable(client_factory) or type(concurrency) is not int or not 1 <= concurrency <= 8:
        raise ValueError("Central broker attestation requires bounded reader concurrency")
    def load():
        client = client_factory()
        try:
            return read_attested_shared_broker_snapshot(client, head_reader, **scope,
                child_client_factory=client_factory, concurrency=concurrency)
        finally:
            client.close()
    return await _owned_worker(load)
