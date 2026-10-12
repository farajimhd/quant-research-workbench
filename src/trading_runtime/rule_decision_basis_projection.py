"""Pure typed projection/audit of staged shared rule-decision references.

Integrity checks do not prove the referenced source or financial prefix. The
future writer must resolve those independently before publishing these rows.
"""
from datetime import datetime
from hashlib import sha256
from uuid import UUID
import re
from collections.abc import Mapping

from src.backend.signal_stream_typed_readback import _normalize
from .journal_contract import canonical_json
from .rule_decision_basis_schema import BASIS, INTENT


def _digest(value):
    return sha256(canonical_json(value).encode()).hexdigest()


def _seal(table, values):
    names = {name for name, _ in table.columns} - {"content_hash"}
    if set(values) != names:
        raise ValueError("Rule decision row columns differ from the shared contract")
    row = {name: str(UUID(str(values[name]))) if kind == "UUID" else
        _normalize(values[name], kind, occurrence=False)
        for name, kind in table.columns if name != "content_hash"}
    return {**row, "content_hash": _digest(row)}


def project_rule_decision_basis(values, intents):
    """Link declared basis references to a bounded ordered semantic intent set."""
    intents = tuple(intents)
    if len(intents) > 16:
        raise ValueError("Rule decision intent set exceeds its bound")
    if any(not isinstance(item, Mapping) or set(item) != {"stage", "strategy_intent_id"} for item in intents):
        raise ValueError("Rule decision intent reference has unmodeled columns")
    clock = values.get("boundary_at")
    clock = datetime.fromisoformat(clock.replace("Z", "+00:00")) if isinstance(clock, str) else clock
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("Rule decision boundary must be timezone-aware")
    common = {key: values[key] for key in ("run_id", "event_month", "batch_id", "record_id", "decision_id")}
    children = tuple(_seal(INTENT, {**common, "ordinal": index,
        "stage": item["stage"], "strategy_intent_id": item["strategy_intent_id"]})
        for index, item in enumerate(intents))
    parent = _seal(BASIS, {**values, "intent_count": len(children), "intent_hash": _digest(children)})
    verify_rule_decision_basis(parent, children)
    return parent, children


def verify_rule_decision_basis(parent, children):
    children = tuple(children)
    if len(children) > 16 or parent != _seal(BASIS, {key: value for key, value in parent.items() if key != "content_hash"}):
        raise ValueError("Rule decision parent integrity differs")
    for key in ("decision_id", "release_content_hash", "source_commit_hash", "history_input_hash", "intent_hash"):
        if re.fullmatch(r"[0-9a-f]{64}", parent[key]) is None:
            raise ValueError("Rule decision reference hash is invalid")
    if (parent["source_batch_sequence"] < 1 or parent["terminal"] not in (0, 1)
            or not parent["event_month"].endswith("-01")
            or not 0 <= parent["basis_sequence"] < parent["decision_sequence"]
            or parent["intent_count"] != len(children) or parent["intent_hash"] != _digest(children)
            or not all(parent[key] for key in ("run_id", "account_id", "assignment_id", "listing_id", "evaluation_block"))):
        raise ValueError("Rule decision scope, sequence or intent coverage differs")
    statuses = {"selected", "wait", "pending_reduction", "pending_acquisition", "awaiting_protection_observation"}
    if parent["selection_status"] not in statuses or (parent["selection_status"] == "selected") != bool(children):
        raise ValueError("Rule decision status differs from selected intents")
    seen = set()
    for index, child in enumerate(children):
        if (child != _seal(INTENT, {key: value for key, value in child.items() if key != "content_hash"})
                or child["ordinal"] != index or child["stage"] not in {"entry", "exit", "add", "reduce", "trail"}
                or not child["strategy_intent_id"] or child["strategy_intent_id"] in seen
                or any(child[key] != parent[key] for key in ("run_id", "event_month", "batch_id", "record_id", "decision_id"))):
            raise ValueError("Rule decision intent linkage differs")
        seen.add(child["strategy_intent_id"])
    return parent, children
