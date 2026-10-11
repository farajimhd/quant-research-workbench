"""Normalized, immutable program releases; children precede the release seal.

This shared producer boundary never installs tables or admits a Backtest. A
storage adapter must provide exclusive publication ownership, SSD/schema
preflight and exact bounded reads. Failed writes stop the lane; no blind retry
is safe for MergeTree. JSON is used only for deterministic in-memory hashing.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from math import isfinite
from types import MappingProxyType
from typing import Mapping, Protocol
from uuid import UUID

from .columnar_rule_program import InputField, Operation, RuleNode, RuleProgram
from .columnar_strategy_schema import RELEASE, FIELD, RULE, NODE, UNIVERSE
from .optimized_program_specification import OptimizedProgramSpecification, STAGES
from src.market_engine.volume_rank_rules import VolumeUniverseRule

TABLES = (FIELD, RULE, NODE, UNIVERSE, RELEASE)
POLICY = ("stop_fraction", "target_fraction", "reduce_fraction", "trail_fraction",
          "add_minimum_profit", "reduce_minimum_profit", "maximum_adds",
          "cooldown_seconds", "swing_left", "swing_right")


def _hash(value) -> str:
    def scalar(item):
        if isinstance(item, Decimal):
            return format(item.normalize(), "f")
        if isinstance(item, UUID):
            return str(item)
        if isinstance(item, datetime):
            return item.astimezone(timezone.utc).isoformat(timespec="microseconds")
        raise TypeError(type(item).__name__)
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             default=scalar, allow_nan=False).encode()).hexdigest()


def _identity(value: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("Release requires an exact SHA256 identity")
    return value


def _cash(value) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite() or result <= 0 or result.as_tuple().exponent < -10:
        raise ValueError("Cash must be positive finite Decimal with at most 10 places")
    if result >= Decimal(10) ** 28:
        raise ValueError("Cash exceeds normalized money contract")
    return result


@dataclass(frozen=True)
class ProgramRelease:
    header: Mapping
    fields: Mapping[str, InputField]
    programs: Mapping[str, RuleProgram]
    minimum_age_seconds: Mapping[str, int]
    universe: VolumeUniverseRule


def project_release(specification: OptimizedProgramSpecification,
                    fields: Mapping[str, InputField], *, strategy_number: int,
                    strategy_id: str, release_id: str, code_hash: str,
                    universe_source_hash: str, published_at: datetime,
                    entry_cash=None, add_cash=None) -> Mapping[str, tuple[Mapping, ...]]:
    """Explicit cash changes create a different immutable content identity."""
    if type(strategy_number) is not int or not 1 <= strategy_number < 2**32:
        raise ValueError("Invalid strategy number")
    if not isinstance(strategy_id, str) or not strategy_id or len(strategy_id) > 128:
        raise ValueError("Invalid strategy identity")
    rid = str(UUID(release_id))
    _identity(code_hash)
    _identity(universe_source_hash)
    _identity(specification.winner_hash)
    if published_at.tzinfo is None or published_at.utcoffset() is None:
        raise ValueError("Release timestamp must be timezone aware")
    if set(specification.programs) != set(STAGES) or not 1 <= len(fields) <= 4096:
        raise ValueError("Release requires five stages and bounded declared inputs")
    if set(specification.policy) != set(POLICY):
        raise ValueError("Release policy fields differ from the shared contract")
    policy = dict(specification.policy)
    for name, value in policy.items():
        if name in {"maximum_adds", "cooldown_seconds", "swing_left", "swing_right"}:
            limit = 2**32 if name == "cooldown_seconds" else 256
            if type(value) is not int or not 0 <= value < limit:
                raise ValueError("Invalid integer release policy")
        elif type(value) not in (float, int) or not isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Invalid fractional release policy")
        else:
            policy[name] = float(value)
    names = sorted(fields)
    if any(name != fields[name].name or fields[name].resolution_ms != 1000 for name in names):
        raise ValueError("Program input identity or evaluation resolution differs")
    field_ids = {name: index for index, name in enumerate(names)}
    rows = {table.name: [] for table in TABLES}
    for name in names:
        field = fields[name]
        rows[FIELD.name].append(dict(release_id=rid, field_id=field_ids[name],
            field_name=name, unit=field.unit, product=field.product,
            product_revision=field.revision, resolution_ms=field.resolution_ms))
    for stage in sorted(STAGES):
        program = specification.programs[stage]
        program.validate(fields)
        age = specification.minimum_age_seconds.get(stage, 0)
        if type(age) is not int or not 0 <= age < 2**32 or (stage == "entry" and age):
            raise ValueError("Invalid position-age restriction")
        opened = int(stage != "entry")
        rows[RULE.name].append(dict(release_id=rid, stage=stage, position_open=opened,
            output_node=program.output, minimum_position_age_seconds=age,
            node_count=len(program.nodes)))
        for index, node in enumerate(program.nodes):
            rows[NODE.name].append(dict(release_id=rid, stage=stage,
                position_open=opened, node_id=index, operation=int(node.operation),
                left_node=node.left if node.left >= 0 else None,
                right_node=node.right if node.right >= 0 else None,
                field_id=field_ids[node.field] if node.operation == Operation.FEATURE else None,
                window=node.window, literal=float(node.literal), literal_unit=node.unit))
    universe = specification.universe
    rows[UNIVERSE.name].append(dict(release_id=rid, resolution_ms=1000,
        volume_window_seconds=universe.window_seconds, top_count=universe.top_count,
        minimum_price=_cash(universe.minimum_prior_close),
        maximum_price=_cash(universe.maximum_prior_close),
        price_filter_before_volume=1, include_current_completed_bucket=1,
        retain_held_positions=1, tie_break_contract="volume-desc/listing-id-asc",
        source_contract_hash=universe_source_hash))
    header = dict(strategy_number=strategy_number, release_id=rid,
        strategy_id=strategy_id, source_winner_hash=specification.winner_hash,
        source_generation=specification.generation, source_candidate=specification.candidate,
        evaluation_interval_ms=1000,
        entry_cash=_cash(specification.entry_cash if entry_cash is None else entry_cash),
        add_cash=_cash(specification.add_cash if add_cash is None else add_cash),
        **policy, universe_contract_hash=_hash(rows[UNIVERSE.name]),
        field_count=len(rows[FIELD.name]), field_hash=_hash(rows[FIELD.name]),
        rule_count=len(rows[RULE.name]), rule_hash=_hash(rows[RULE.name]),
        node_count=len(rows[NODE.name]), node_hash=_hash(rows[NODE.name]),
        code_hash=code_hash, published_at=published_at.astimezone(timezone.utc))
    header["content_hash"] = _hash(header)
    rows[RELEASE.name].append(header)
    result = MappingProxyType({name: tuple(MappingProxyType(row) for row in family)
                               for name, family in rows.items()})
    restore_release(result)
    return result


def restore_release(rows: Mapping[str, tuple[Mapping, ...]]) -> ProgramRelease:
    """Reject orphan, duplicate, altered or partial rows before admission."""
    if set(rows) != {t.name for t in TABLES} or len(rows[RELEASE.name]) != 1:
        raise ValueError("Release requires exactly one seal and complete child families")
    copied = {name: [dict(row) for row in family] for name, family in rows.items()}
    header = copied[RELEASE.name][0]
    if (type(header.get("strategy_number")) is not int or not 1 <= header["strategy_number"] < 2**32
            or not isinstance(header.get("strategy_id"), str) or not header["strategy_id"]
            or len(header["strategy_id"]) > 128
            or type(header.get("source_generation")) is not int or header["source_generation"] < 0
            or type(header.get("source_candidate")) is not int or header["source_candidate"] < 0):
        raise ValueError("Invalid release identity")
    UUID(str(header["release_id"]))
    for name in ("source_winner_hash", "code_hash", "content_hash"):
        _identity(header[name])
    for name in ("entry_cash", "add_cash"):
        if not isinstance(header[name], Decimal) or _cash(header[name]) != header[name]:
            raise ValueError("Release money has an invalid normalized type")
    at = header["published_at"]
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("Release timestamp must be timezone aware")
    for name in POLICY:
        value = header[name]
        if name in {"maximum_adds", "cooldown_seconds", "swing_left", "swing_right"}:
            limit = 2**32 if name == "cooldown_seconds" else 256
            if type(value) is not int or not 0 <= value < limit:
                raise ValueError("Invalid integer release policy")
        elif type(value) is not float or not isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Invalid fractional release policy")
    if not 1 <= len(copied[FIELD.name]) <= 4096 or len(copied[NODE.name]) > 160:
        raise ValueError("Release exceeds bounded contract")
    for table in TABLES:
        expected = {name for name, _ in table.columns}
        if any(set(row) != expected or str(row["release_id"]) != str(header["release_id"])
               for row in copied[table.name]):
            raise ValueError("Release columns or child foreign keys differ")
    for table, prefix in ((FIELD, "field"), (RULE, "rule"), (NODE, "node")):
        if (len(copied[table.name]) != header[prefix + "_count"]
                or _hash(copied[table.name]) != header[prefix + "_hash"]):
            raise ValueError("Release child counts or hashes differ")
    if (len(copied[UNIVERSE.name]) != 1
            or _hash(copied[UNIVERSE.name]) != header["universe_contract_hash"]
            or _hash({k: v for k, v in header.items() if k != "content_hash"}) != header["content_hash"]):
        raise ValueError("Release seal hash differs")
    fields, by_id = {}, {}
    for index, row in enumerate(copied[FIELD.name]):
        if (type(row["field_id"]) is not int or row["field_id"] != index
                or row["field_name"] in fields or row["resolution_ms"] != 1000):
            raise ValueError("Duplicate or unordered release fields")
        field = InputField(row["field_name"], row["unit"], row["product"],
                           row["product_revision"], row["resolution_ms"])
        fields[field.name] = field
        by_id[index] = field.name
    programs, ages = {}, {}
    consumed = 0
    for row in copied[RULE.name]:
        stage = row["stage"]
        if stage not in STAGES or stage in programs or row["position_open"] != int(stage != "entry"):
            raise ValueError("Duplicate or invalid release stage")
        nodes = [node for node in copied[NODE.name] if node["stage"] == stage
                 and node["position_open"] == row["position_open"]]
        if len(nodes) != row["node_count"]:
            raise ValueError("Release node coverage differs")
        instructions = []
        for index, node in enumerate(nodes):
            if node["node_id"] != index:
                raise ValueError("Duplicate or unordered release nodes")
            op = Operation(node["operation"])
            if (op == Operation.FEATURE) != (node["field_id"] is not None):
                raise ValueError("Rule feature foreign key differs")
            instructions.append(RuleNode(op, -1 if node["left_node"] is None else node["left_node"],
                -1 if node["right_node"] is None else node["right_node"],
                by_id[node["field_id"]] if op == Operation.FEATURE else "",
                node["window"], node["literal"], node["literal_unit"]))
        program = RuleProgram(tuple(instructions), row["output_node"])
        program.validate(fields)
        age = row["minimum_position_age_seconds"]
        if type(age) is not int or not 0 <= age < 2**32 or (stage == "entry" and age):
            raise ValueError("Invalid position-age restriction")
        programs[stage] = program
        ages[stage] = row["minimum_position_age_seconds"]
        consumed += len(nodes)
    if set(programs) != set(STAGES) or consumed != len(copied[NODE.name]):
        raise ValueError("Release has missing stages or orphan nodes")
    u = copied[UNIVERSE.name][0]
    _identity(u["source_contract_hash"])
    if (header["evaluation_interval_ms"] != 1000 or u["resolution_ms"] != 1000
            or tuple(u[name] for name in ("price_filter_before_volume",
                "include_current_completed_bucket", "retain_held_positions")) != (1, 1, 1)
            or u["tie_break_contract"] != "volume-desc/listing-id-asc"):
        raise ValueError("Release scanner semantics differ")
    universe = VolumeUniverseRule(u["volume_window_seconds"], u["top_count"],
        float(u["minimum_price"]), float(u["maximum_price"]))
    return ProgramRelease(MappingProxyType(header), MappingProxyType(fields),
        MappingProxyType(programs), MappingProxyType(ages), universe)


class ReleaseStorage(Protocol):
    """Exclusive producer authority, never supplied by a strategy executor."""
    def preflight(self) -> None: ...
    def assert_owner(self) -> None: ...
    def read_number(self, strategy_number: int) -> Mapping[str, tuple[Mapping, ...]] | None: ...
    def read_children(self, release_id: str) -> Mapping[str, tuple[Mapping, ...]]: ...
    def insert_rows(self, table_name: str, rows: tuple[Mapping, ...]) -> None: ...


class ReleasePublisher:
    """Bounded single publication lane with nonblocking caller-side I/O."""
    def __init__(self, storage: ReleaseStorage):
        self._storage = storage
        self._busy = False
        self._failed = False

    async def publish(self, rows: Mapping[str, tuple[Mapping, ...]]) -> ProgramRelease:
        # Validate/snapshot before thread handoff; mutable caller objects cannot
        # race the publication and ambiguous writes permanently stop this lane.
        snapshot = {name: tuple(dict(row) for row in family) for name, family in rows.items()}
        release = restore_release(snapshot)
        if self._busy or self._failed:
            raise RuntimeError("Release publication lane is busy or stopped")
        self._busy = True
        task = asyncio.create_task(asyncio.to_thread(self._publish, snapshot, release))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Do not release ownership while a blocking insert is still active.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if task.done() and not task.cancelled():
                task.exception()
            self._failed = True
            raise
        except BaseException:
            self._failed = True
            raise
        finally:
            self._busy = False

    def _publish(self, rows, release):
        s = self._storage
        s.preflight()
        s.assert_owner()
        existing = s.read_number(release.header["strategy_number"])
        if existing is not None:
            restored = restore_release(existing)
            if restored.header["content_hash"] != release.header["content_hash"]:
                raise ValueError("Strategy number already has a different immutable release")
            return restored
        prior = s.read_children(release.header["release_id"])
        if any(prior.values()):
            raise ValueError("Unsealed release children require explicit recovery; refusing retry")
        for table in TABLES[:-1]:
            s.assert_owner()
            s.insert_rows(table.name, rows[table.name])
        readback = s.read_children(release.header["release_id"])
        if {name: list(family) for name, family in readback.items()} != {
                t.name: list(rows[t.name]) for t in TABLES[:-1]}:
            raise ValueError("Release child readback differs; seal withheld")
        s.assert_owner()
        s.insert_rows(RELEASE.name, rows[RELEASE.name])
        restored = restore_release(s.read_number(release.header["strategy_number"]))
        if restored.header["content_hash"] != release.header["content_hash"]:
            raise ValueError("Published release seal differs")
        s.assert_owner()
        return restored
