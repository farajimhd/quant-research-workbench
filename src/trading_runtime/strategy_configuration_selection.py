"""Pure normalized configuration projection and integrity verification.

No publication, database access, release registration or launch occurs here.
The caller must separately verify referenced programme and policy authorities.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite
from types import MappingProxyType
from uuid import UUID
import re

from .journal_contract import canonical_json
from . import strategy_configuration_schema as schema

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MODES = frozenset({"live", "paper", "replay", "backtest", "backtest_debug"})
_FAMILIES = ("account", "mode", "mandate", "group", "group_member", "supervisor")
_KEYS = (("ordinal",), ("account_key", "ordinal"), ("ordinal",),
         ("ordinal",), ("group_id", "ordinal"), ("mode",))


def _hash(value):
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _row(table, source):
    if set(source) != {name for name, _ in table.columns}:
        raise ValueError("Configuration row has missing or extra defined fields")
    row = dict(source)
    for name, kind in table.columns:
        value = row[name]
        if kind == "UUID":
            if type(value) is not str or str(UUID(value)) != value:
                raise ValueError("Configuration UUID is not canonical")
        elif kind.startswith("UInt"):
            if type(value) is not int or not 0 <= value < 2 ** int(kind[4:]):
                raise ValueError("Configuration integer exceeds its typed domain")
            if name in {"enabled", "allow_replacement"} and value not in (0, 1):
                raise ValueError("Configuration boolean exceeds its typed domain")
        elif kind == "Float64":
            if type(value) is not float or not isfinite(value) or value < 0:
                raise ValueError("Configuration numeric field must be finite and nonnegative")
            if name.endswith("_fraction") or name == "strategy_allocation":
                if value > 1:
                    raise ValueError("Configuration fraction exceeds one")
        elif kind == "FixedString(64)":
            if type(value) is not str or not _HASH.fullmatch(value):
                raise ValueError("Configuration hash is invalid")
        elif kind.startswith("DateTime64"):
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Configuration timestamp must be timezone aware")
            row[name] = value.astimezone(timezone.utc)
        elif kind in {"String", "LowCardinality(String)"}:
            if type(value) is not str or len(value) > 256:
                raise ValueError("Configuration text exceeds its typed domain")
        else:
            raise ValueError("Unsupported configuration field type")
    return row


@dataclass(frozen=True, slots=True)
class StrategyConfigurationSelection:
    header: object
    rows: object


def _ordinals(rows, owner=None):
    owners = {row[owner] for row in rows} if owner else {None}
    for identity in owners:
        selected = [row for row in rows if not owner or row[owner] == identity]
        if [row["ordinal"] for row in selected] != list(range(len(selected))):
            raise ValueError("Configuration ordinals are incomplete or duplicated")


def restore_configuration_selection(source):
    """Verify all sealed fields and relationships; return an immutable packet."""
    if set(source) != {table.name for table in schema.TABLES}:
        raise ValueError("Configuration requires every defined family")
    if any(type(rows) not in (tuple, list) or len(rows) > 4096 for rows in source.values()):
        raise ValueError("Configuration family exceeds its bounded row budget")
    rows = {table.name: tuple(_row(table, row) for row in source[table.name])
            for table in schema.TABLES}
    if len(rows[schema.SELECTION.name]) != 1:
        raise ValueError("Configuration requires exactly one immutable seal")
    header = rows[schema.SELECTION.name][0]
    if (not header["strategy_id"] or not header["run_plan_id"] or not header["label"]
            or header["strategy_revision"] < 1
            or _hash({key: value for key, value in header.items() if key != "content_hash"}) != header["content_hash"]):
        raise ValueError("Configuration seal identity or content hash differs")
    for family, table, keys in zip(_FAMILIES, schema.CHILDREN, _KEYS, strict=True):
        ordered = tuple(sorted(rows[table.name], key=lambda row: tuple(row[key] for key in keys)))
        rows[table.name] = ordered
        if (any(row["configuration_id"] != header["configuration_id"] for row in ordered)
                or len(ordered) != header[family + "_count"]
                or _hash(ordered) != header[family + "_hash"]):
            raise ValueError("Configuration child scope, count or hash differs")
    accounts, modes, mandates, groups, members, supervisors = (rows[table.name] for table in schema.CHILDREN)
    for family in (accounts, mandates, groups):
        _ordinals(family)
    _ordinals(modes, "account_key")
    _ordinals(members, "group_id")
    account_keys = {row["account_key"] for row in accounts}
    group_ids = {row["group_id"] for row in groups}
    if (not accounts or len(account_keys) != len(accounts) or "" in account_keys
            or any(not row["account_class"] or not row["base_currency"] for row in accounts)
            or len(group_ids) != len(groups) or "" in group_ids
            or len({row["mandate_id"] for row in mandates}) != len(mandates)):
        raise ValueError("Configuration account, group or mandate identity is empty or repeated")
    if (any(row["account_key"] not in account_keys or row["mode"] not in _MODES for row in modes)
            or len({(row["account_key"], row["mode"]) for row in modes}) != len(modes)
            or {row["account_key"] for row in modes} != account_keys
            or any(row["mode"] not in _MODES for row in supervisors)
            or len({row["mode"] for row in supervisors}) != len(supervisors)
            or {row["mode"] for row in modes} - {row["mode"] for row in supervisors}):
        raise ValueError("Configuration modes or supervisor coverage differ")
    if (any(row["account_key"] not in account_keys or row["run_plan_id"] != header["run_plan_id"]
            or not row["mandate_id"] or not row["assignment_mode"]
            or not row["maximum_action_authority"] for row in mandates)
            or len({row["account_key"] for row in mandates}) != len(mandates)):
        raise ValueError("Configuration mandate scope is invalid or ambiguous")
    if (any(row["group_id"] not in group_ids or row["account_key"] not in account_keys for row in members)
            or len({(row["group_id"], row["account_key"]) for row in members}) != len(members)
            or {row["group_id"] for row in members} != group_ids):
        raise ValueError("Configuration group membership is orphaned, empty or repeated")
    frozen = MappingProxyType({name: tuple(MappingProxyType(row) for row in family)
                              for name, family in rows.items()})
    return StrategyConfigurationSelection(frozen[schema.SELECTION.name][0], frozen)


def prepare_configuration_selection(*, configuration_id, label, run_plan_id,
        strategy_id, strategy_revision, program_release_id, program_release_hash,
        oms_limit_offset_bps, published_at, children):
    """Seal explicitly supplied typed selections without choosing any settings."""
    if set(children) != {table.name for table in schema.CHILDREN}:
        raise ValueError("Configuration projection needs every child family")
    header = dict(configuration_id=configuration_id, label=label, run_plan_id=run_plan_id,
        strategy_id=strategy_id, strategy_revision=strategy_revision,
        program_release_id=program_release_id, program_release_hash=program_release_hash,
        oms_limit_offset_bps=oms_limit_offset_bps,
        published_at=published_at)
    rows = {}
    for family, table, keys in zip(_FAMILIES, schema.CHILDREN, _KEYS, strict=True):
        if type(children[table.name]) not in (tuple, list) or len(children[table.name]) > 4096:
            raise ValueError("Configuration family exceeds its bounded row budget")
        selected = tuple(sorted((_row(table, row) for row in children[table.name]),
                                key=lambda row: tuple(row[key] for key in keys)))
        rows[table.name] = selected
        header[family + "_count"] = len(selected)
        header[family + "_hash"] = _hash(selected)
    # Canonical UTC timestamp must participate in the seal itself.
    header = _row(schema.SELECTION, {**header, "content_hash": "0" * 64})
    header["content_hash"] = _hash({key: value for key, value in header.items() if key != "content_hash"})
    rows[schema.SELECTION.name] = (header,)
    return restore_configuration_selection(rows)
