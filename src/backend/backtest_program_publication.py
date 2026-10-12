"""Bounded native qualification retention for the current typed publisher."""
from threading import RLock
from weakref import WeakKeyDictionary

_LOCK = RLock()
_PENDING = WeakKeyDictionary()


def retain_qualified_rule_decision(publisher, qualified):
    from .backtest_typed_publisher import BacktestTypedJournalPublisher
    from src.trading_runtime.rule_decision_publication import QualifiedRuleDecisionBatch, _check_rule_decision_qualification
    from src.trading_runtime.rule_decision_basis_projection import RuleDecisionBasisBatch
    if (type(publisher) is not BacktestTypedJournalPublisher
            or publisher.writer.journal_profile != "backtest_v4"
            or type(qualified) is not QualifiedRuleDecisionBatch
            or publisher.journal.run_id != qualified.base.run_id):
        raise ValueError("Rule proof retention requires its exact V4 publisher")
    _check_rule_decision_qualification(qualified.unit, qualified.qualification)
    sequence = qualified.unit.basis["decision_sequence"]
    records = publisher.journal.unfenced_records(after_sequence=sequence - 1, through_sequence=sequence)
    if len(records) != 1 or records[0].record_id != qualified.unit.basis["record_id"]:
        raise ValueError("Rule proof has no exact pending completion")
    pending = RuleDecisionBasisBatch.from_pending_completion(publisher.journal, records[0], qualified.base)
    _check_rule_decision_qualification(pending, qualified.qualification)
    with _LOCK:
        if publisher.fenced_sequence >= sequence:
            raise ValueError("Rule proof completion was already fenced")
        retained = _PENDING.setdefault(publisher, {})
        identity = records[0].record_id
        if identity in retained:
            raise ValueError("Pending rule proof cannot be replaced")
        if len(retained) >= publisher.journal.pending_record_count:
            raise ValueError("Rule proof retention exceeds pending journal bound")
        retained[identity] = qualified.qualification


def program_decision_projection_arguments(publisher, through_sequence):
    records = publisher.journal.unfenced_records(after_sequence=publisher.fenced_sequence,
                                                through_sequence=through_sequence)
    ids = {record.record_id for record in records
           if record.entity_type == "signal" and record.payload.get("signal_type") == "program_boundary_completed"}
    with _LOCK:
        retained = _PENDING.get(publisher)
        if retained is None:
            return {}
        return {"rule_decision_qualifications": {identity: retained[identity] for identity in ids
                                                if identity in retained}}


def release_fenced_rule_decisions(publisher, batch):
    if publisher.fenced_sequence < batch.last_sequence:
        raise ValueError("Rule proofs cannot be released before publisher acknowledgement")
    with _LOCK:
        retained = _PENDING.get(publisher)
        if retained is not None:
            for event in batch.events:
                retained.pop(event["record_id"], None)
            if not retained:
                del _PENDING[publisher]
