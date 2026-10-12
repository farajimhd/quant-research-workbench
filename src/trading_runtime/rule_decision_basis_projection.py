"""Pure typed projection/audit of staged shared rule-decision references.

Integrity checks do not prove the referenced source or financial prefix. The
future writer must resolve those independently before publishing these rows.
"""
from datetime import datetime
from hashlib import sha256
from uuid import UUID
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from src.backend.signal_stream_typed_readback import _normalize
from .journal_contract import canonical_json
from .rule_decision_basis_schema import BASIS, INTENT


@dataclass(frozen=True, slots=True)
class RuleDecisionBasisBatch:
    """Staged scalar supplement retaining the original typed journal batch.

    Active writers must explicitly support this family before it can be sent.
    Constructing a transport does not prove source or financial-prefix authority.
    """
    base: object
    basis: Mapping
    intents: tuple[Mapping, ...]

    def __post_init__(self):
        from .arte_journal_writer import TypedJournalBatch
        parent, children = verify_rule_decision_basis(self.basis, self.intents)
        base = self.base
        if (type(base) is not TypedJournalBatch or len(base.events) != 1
                or base.run_id != parent["run_id"] or base.batch_id != parent["batch_id"]
                or base.first_sequence != parent["decision_sequence"]
                or base.last_sequence != parent["decision_sequence"]):
            raise ValueError("Rule decision transport differs from its typed batch")
        event = base.events[0]
        if (any(event.get(key) != parent[key] for key in
                ("run_id", "batch_id", "record_id", "account_id", "event_month"))
                or event.get("sequence") != parent["decision_sequence"]
                or event.get("entity_id") != parent["decision_id"]
                or (event.get("category"), event.get("entity_type")) != ("strategy_decision", "signal")
                or _normalize(event.get("event_time"), "DateTime64(6, 'UTC')", occurrence=False) != parent["boundary_at"]
                or event.get("causation_id") != parent["source_commit_hash"]):
            raise ValueError("Rule decision transport differs from its event envelope")
        if len(base.signals) != 1:
            raise ValueError("Rule decision transport lacks its completion detail")
        detail = base.signals[0]
        if (any(detail.get(key) != parent[key] for key in
                ("run_id", "batch_id", "record_id", "account_id", "event_month"))
                or detail.get("signal_id") != parent["decision_id"]
                or detail.get("signal_type") != "program_boundary_completed"
                or detail.get("decision_assignment_id") != parent["assignment_id"]
                or detail.get("decision_status") != "completed"
                or detail.get("decision_reason_detail") != parent["selection_status"]
                or _normalize(detail.get("source_event_time"), "DateTime64(6, 'UTC')", occurrence=False)
                   != parent["boundary_at"]):
            raise ValueError("Rule decision transport differs from its completion detail")
        object.__setattr__(self, "basis", MappingProxyType(dict(parent)))
        object.__setattr__(self, "intents", tuple(MappingProxyType(dict(child)) for child in children))


def project_program_completion_basis(record, *, release, block, assignment,
        listing_id, ordinal, source_boundary, basis_sequence, intents, terminal, batch_id):
    """Bind a completed runtime record to its prepared input/source references.

    This validates linkage only. Independent replay still proves the selected
    intents; no proposal-supplied value chooses a saved-run market authority.
    """
    from src.backend.backtest_program_inputs import PreparedProgramBlock
    from src.backend.signal_stream_runtime_service import signal_stream_session
    from .columnar_strategy_release import ProgramRelease
    from .journal_contract import JournalRecord
    from .strategy_engine import StrategyAssignment
    if (type(record) is not JournalRecord or type(block) is not PreparedProgramBlock
            or type(release) is not ProgramRelease or type(assignment) is not StrategyAssignment
            or type(terminal) is not bool or type(ordinal) is not int
            or type(source_boundary) is not tuple or len(source_boundary) != 3):
        raise ValueError("Program completion basis requires typed runtime references")
    sequence, source_hash, at = source_boundary
    row, _ = block.row_for(listing_id, ordinal)
    payload = record.payload
    if ((record.category, record.entity_type, payload.get("signal_type"))
            != ("strategy_decision", "signal", "program_boundary_completed")
            or record.event_time != at or row["boundary_at"] != at
            or record.account_id != assignment.account_id
            or payload.get("ticker") != assignment.ticker
            or payload.get("causation_id") != source_hash
            or (payload.get("strategy_id"), payload.get("strategy_revision"))
               != (assignment.strategy_id, assignment.strategy_revision)
            or (release.header["strategy_id"], release.header["strategy_number"])
               != (assignment.strategy_id, assignment.strategy_revision)
            or block.release_hash != release.header["content_hash"]
            or record.entity_id != block.completion_identity(run_id=record.run_id,
                assignment_id=assignment.assignment_id, source_hash=source_hash,
                listing_id=listing_id, ordinal=ordinal, terminal=terminal)):
        raise ValueError("Program completion basis differs from its prepared source")
    status = payload["metadata"]["reason_detail"]
    return project_rule_decision_basis(dict(run_id=record.run_id,
        event_month=at.date().replace(day=1), batch_id=batch_id, record_id=record.record_id,
        decision_id=record.entity_id, account_id=assignment.account_id,
        assignment_id=assignment.assignment_id, listing_id=listing_id,
        release_id=release.header["release_id"], release_content_hash=block.release_hash,
        source_session_date=signal_stream_session(at)["session_key"], source_batch_sequence=sequence,
        source_commit_hash=source_hash, history_input_hash=block.history_hashes[listing_id],
        evaluation_block=block.evaluation_block, observed_ordinal=ordinal, boundary_at=at,
        basis_sequence=basis_sequence, decision_sequence=record.sequence,
        terminal=int(terminal), selection_status=status), intents)


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
            or len(children) >= parent["decision_sequence"] - parent["basis_sequence"]
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


def verify_rule_decision_intent_records(parent, children, records):
    """Join exact normalized semantic intent records inside the basis interval."""
    from .arte_intent_projection import RecoveredIntent
    parent, children = verify_rule_decision_basis(parent, children)
    records = tuple(records)
    if (len(records) != len(children) or any(type(record) is not RecoveredIntent for record in records)
            or len({record.intent.intent_id for record in records}) != len(records)
            or len({record.record_id for record in records}) != len(records)
            or len({record.sequence for record in records}) != len(records)):
        raise ValueError("Rule decision intent records are missing or duplicate")
    by_id = {record.intent.intent_id: record for record in records}
    at = datetime.fromisoformat(parent["boundary_at"])
    for child in children:
        record = by_id.get(child["strategy_intent_id"])
        if (record is None or record.account_id != parent["account_id"]
                or not parent["basis_sequence"] < record.sequence < parent["decision_sequence"]
                or record.intent.event_time != at or record.intent.metadata):
            raise ValueError("Rule decision intent record differs from its causal interval")
    return tuple(by_id[child["strategy_intent_id"]] for child in children)


def verify_rule_decision_completion_record(parent, children, record):
    """Match typed decision references to the exact journal completion event."""
    from .journal_contract import JournalRecord
    parent, children = verify_rule_decision_basis(parent, children)
    if type(record) is not JournalRecord:
        raise ValueError("Rule decision completion requires a journal record")
    payload = record.payload
    if ((record.category, record.entity_type, payload.get("signal_type"))
            != ("strategy_decision", "signal", "program_boundary_completed")
            or record.run_id != parent["run_id"]
            or record.record_id != parent["record_id"]
            or record.entity_id != parent["decision_id"]
            or record.account_id != parent["account_id"]
            or record.sequence != parent["decision_sequence"]
            or record.event_time != datetime.fromisoformat(parent["boundary_at"])
            or payload.get("causation_id") != parent["source_commit_hash"]
            or payload.get("metadata", {}).get("assignment_id") != parent["assignment_id"]
            or payload.get("metadata", {}).get("reason_detail") != parent["selection_status"]):
        raise ValueError("Rule decision completion differs from its journal authority")
    return record
