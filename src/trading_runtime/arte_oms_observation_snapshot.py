"""Shared, bit-exact OMS observations for future strategy checkpoints.

These inert contracts do not install tables or grant restart admission. The
legacy codec supplies fingerprint shape validation and pure actor restoration;
no legacy table or Keeper namespace is read or written. Quantity bits here are
OMS deduplication observations, not a replacement for financial quantity ledgers.
Zero follows the existing shared scalar codec's canonical positive-zero rule.
"""
from dataclasses import dataclass, replace
from types import MappingProxyType, SimpleNamespace
from collections.abc import Mapping
from datetime import date
from uuid import NAMESPACE_URL, uuid5
import json
import re

from . import strategy_one_oms_observation_snapshot as codec
from .trading_price_codec import encode_trading_price, decode_trading_price

_NUMBERS = ("filled_quantity", "remaining_quantity", "average_fill_price",
            "limit_price", "stop_price")
ROOT = replace(codec.ROOT, name="trading_oms_observation_snapshot_v1")
OBSERVATION = replace(codec.OBSERVATION, name="trading_oms_observation_v1",
    columns=tuple((name + "_bits", "UInt64") if name in _NUMBERS else (name, kind)
                  for name, kind in codec.OBSERVATION.columns))
TABLES = (ROOT, OBSERVATION)


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
