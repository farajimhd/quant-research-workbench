"""Bounded native qualification retention for the current typed publisher."""
from threading import RLock
from weakref import WeakKeyDictionary

_LOCK = RLock()
_PENDING = WeakKeyDictionary()


def program_publication_cut(publisher, target_sequence):
    """Keep qualified decision spans atomic within the existing event budget."""
    end = min(target_sequence, publisher.fenced_sequence + publisher.batch_size)
    with _LOCK:
        retained = tuple((_PENDING.get(publisher) or {}).items())
    if not retained:
        return end
    if publisher.writer.journal_profile != "backtest_v4":
        raise ValueError("Program publication cut requires its V4 owner")
    from src.trading_runtime.rule_decision_basis_projection import rule_decision_semantic_hash
    for identity, proof in retained:
        basis = publisher.journal.rule_decision_basis_for_record(identity)
        if basis is None:
            raise ValueError("Program publication cut lost its retained decision basis")
        parent, children = basis
        first, last = parent["basis_sequence"], parent["decision_sequence"]
        if first < target_sequence < last or first < end < last:
            if (proof.run_id != publisher.journal.run_id or proof.basis_sequence != first
                    or proof.semantic_hash != rule_decision_semantic_hash(parent, children)):
                raise ValueError("Program publication cut differs from qualified evidence")
            if first < target_sequence < last:
                raise ValueError("Requested publication fence truncates a qualified program decision")
            end = first
    if end <= publisher.fenced_sequence:
        raise ValueError("Qualified program decision exceeds the existing writer event budget")
    return end


def cache_committed_program_intent_sources(publisher, batch, unit):
    """Retain exact acknowledged source revisions for later OMS transitions."""
    from .backtest_typed_publisher import _committed_intent_source
    from src.trading_runtime.arte_journal_compound_v4 import V4CompoundBatch
    from src.trading_runtime.rule_decision_publication import QualifiedRuleDecisionBatch, _check_rule_decision_qualification
    from src.trading_runtime.arte_intent_projection import ProjectedIntent, restore_strategy_intent
    from src.trading_runtime.arte_journal_writer import TypedJournalBatch
    base = unit if type(unit) is TypedJournalBatch else unit.base
    if base is not batch:
        raise ValueError("Committed program cache requires its exact acknowledged batch")
    units = unit.units if type(unit) is V4CompoundBatch else (unit,)
    wanted = set()
    for child in units:
        if type(child) is QualifiedRuleDecisionBatch:
            _check_rule_decision_qualification(child.unit, child.qualification)
            wanted.update(row["strategy_intent_id"] for row in child.unit.intents)
    if not wanted:
        return
    if publisher.writer.journal_profile != "backtest_v4" or batch.run_id != publisher.journal.run_id:
        raise ValueError("Committed program cache requires its owned V4 run")
    prepared = {}
    for row in batch.intents:
        if row["intent_id"] not in wanted:
            continue
        source = _committed_intent_source(batch, row["record_id"])
        excluded = {"record_id", "parent_record_id", "run_id", "event_month", "batch_id", "account_id", "content_hash"}
        core = {key: value for key, value in row.items() if key not in excluded}
        core["event_time"] = source.events[0]["event_time"]
        slices = tuple({"intent_id": row["intent_id"], **{key: value for key, value in item.items()
            if key not in excluded}} for item in source.intent_slices)
        intent = restore_strategy_intent(ProjectedIntent(core, slices))
        if intent.intent_id in prepared:
            raise ValueError("Committed program batch repeats a semantic intent")
        prior = publisher._committed_strategy_intents.get(intent.intent_id)
        if prior is not None and prior != (source, intent):
            raise ValueError("Committed program intent cannot replace its acknowledged source")
        prepared[intent.intent_id] = (source, intent)
    publisher._committed_strategy_intents.update(prepared)


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


async def qualify_pending_program_decision(controller, candidate, record_id, records, *,
        cold, prefix, broker, oms_image, packet, block, population,
        execution_policy, protection_profile, outside_rth):
    """Qualify one pending completion through the controller's native checks.

    This performs qualification reads only and retains its result in memory.
    Publication remains the existing publisher's explicit next operation.
    """
    from .replay_run_service import ReplayRunController
    from .backtest_typed_publisher import BacktestTypedJournalPublisher
    from src.trading_runtime.arte_journal_projection import project_journal_record
    from src.trading_runtime.rule_decision_basis_projection import RuleDecisionBasisBatch
    from src.trading_runtime.rule_decision_publication import QualifiedRuleDecisionBatch
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    if type(controller) is not ReplayRunController:
        raise TypeError("Pending decision qualification requires the native controller")
    publisher, journal = controller._journal_publisher, controller._journal
    runtime = controller._runtime
    def require_scope():
        if (type(publisher) is not BacktestTypedJournalPublisher
                or controller._journal_publisher is not publisher or controller._journal is not journal
                or controller._runtime is not runtime or runtime is None or runtime.journal is not journal
                or publisher.journal is not journal or publisher.writer.journal_profile != "backtest_v4"
                or publisher._error is not None or publisher._terminal_task is not None
                or publisher._task is not None and not publisher._task.done()):
            raise ValueError("Pending decision qualification lost its idle owned publisher")
    require_scope()
    basis = journal.rule_decision_basis_for_record(record_id)
    if basis is None:
        raise ValueError("Pending decision qualification lacks retained causal evidence")
    parent, children = basis
    if (type(prefix) is not V4CommittedPrefix or prefix.run_id != journal.run_id
            or prefix.status != "running" or not prefix.batch_ids
            or prefix.last_sequence != publisher.fenced_sequence
            or prefix.last_batch_id != publisher._batch_id
            or prefix.last_batch_id != prefix.batch_ids[-1]
            or not 1 <= parent["basis_sequence"] <= prefix.last_sequence):
        raise ValueError("Pending qualification requires the committed decision basis at its owned publisher fence")
    sequence = parent["decision_sequence"]
    pending = journal.unfenced_records(after_sequence=sequence - 1, through_sequence=sequence)
    if len(pending) != 1 or pending[0].record_id != record_id:
        raise ValueError("Pending decision qualification has no exact completion")
    completion = pending[0]
    qualified = await controller.qualify_program_decision_candidate(candidate, parent, children, records,
        cold=cold, prefix=prefix, broker=broker, oms_image=oms_image, packet=packet,
        block=block, population=population, execution_policy=execution_policy,
        protection_profile=protection_profile, outside_rth=outside_rth)
    require_scope()
    if journal.unfenced_records(after_sequence=sequence - 1, through_sequence=sequence) != pending:
        raise ValueError("Pending completion changed during native qualification")
    base = project_journal_record(completion, run_month=publisher.run_month,
        attempt_id=publisher.attempt_id, batch_id=parent["batch_id"],
        prior_batch_id=publisher._batch_id, source_cursor=publisher._source_cursor,
        expected_mode="backtest", exact_signal_prices=publisher.exact_oms_prices)
    envelope = QualifiedRuleDecisionBatch(RuleDecisionBasisBatch.from_pending_completion(journal, completion, base),
                                         qualified)
    retain_qualified_rule_decision(publisher, envelope)
    return envelope


async def qualify_captured_pending_program_decision(controller, candidate, record_id, records, *,
        cold, capture, packet, block, population, execution_policy, protection_profile, outside_rth):
    """Retain a native-qualified pending suffix for the existing V4 publisher."""
    from .replay_run_service import ReplayRunController
    from .backtest_typed_publisher import BacktestTypedJournalPublisher
    from .rule_decision_reselection import qualify_captured_rule_decision_runtime
    from src.trading_runtime.arte_journal_projection import project_journal_record
    from src.trading_runtime.rule_decision_basis_projection import RuleDecisionBasisBatch
    from src.trading_runtime.rule_decision_publication import QualifiedRuleDecisionBatch
    if type(controller) is not ReplayRunController:
        raise TypeError("Pending capture qualification requires the native controller")
    publisher, journal, runtime = controller._journal_publisher, controller._journal, controller._runtime
    if type(publisher) is not BacktestTypedJournalPublisher:
        raise ValueError("Pending capture qualification lacks its owned V4 publisher")
    fence = (publisher.fenced_sequence, publisher._batch_id, publisher._source_cursor)
    def require_scope():
        if (controller._journal_publisher is not publisher or controller._journal is not journal
                or controller._runtime is not runtime or runtime is None or runtime.journal is not journal
                or publisher.journal is not journal or publisher.writer.journal_profile != "backtest_v4"
                or publisher._error is not None or publisher._terminal_task is not None
                or publisher._task is not None and not publisher._task.done()
                or (publisher.fenced_sequence, publisher._batch_id, publisher._source_cursor) != fence):
            raise ValueError("Pending capture qualification lost its idle owned publisher")
    require_scope()
    basis = journal.rule_decision_basis_for_record(record_id)
    if basis is None:
        raise ValueError("Pending capture qualification lacks retained causal evidence")
    parent, children = basis
    sequence = parent["decision_sequence"]
    pending = journal.unfenced_records(after_sequence=sequence - 1, through_sequence=sequence)
    if len(pending) != 1 or pending[0].record_id != record_id:
        raise ValueError("Pending capture qualification has no exact completion")
    completion = pending[0]
    qualified = await qualify_captured_rule_decision_runtime(parent, children, records,
        controller=controller, runtime=candidate, cold=cold, capture=capture, packet=packet,
        block=block, population=population, source=controller._program_signal_runtime,
        execution_policy=execution_policy, protection_profile=protection_profile, outside_rth=outside_rth)
    require_scope()
    if (journal.rule_decision_basis_for_record(record_id) != basis
            or journal.unfenced_records(after_sequence=sequence - 1, through_sequence=sequence) != pending):
        raise ValueError("Pending completion changed during captured qualification")
    base = project_journal_record(completion, run_month=publisher.run_month,
        attempt_id=publisher.attempt_id, batch_id=parent["batch_id"],
        prior_batch_id=publisher._batch_id, source_cursor=publisher._source_cursor,
        expected_mode="backtest", exact_signal_prices=publisher.exact_oms_prices)
    envelope = QualifiedRuleDecisionBatch(RuleDecisionBasisBatch.from_pending_completion(journal, completion, base),
        qualified)
    retain_qualified_rule_decision(publisher, envelope)
    return envelope
