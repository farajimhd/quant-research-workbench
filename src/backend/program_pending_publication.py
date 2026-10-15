"""Publish bounded staged program facts through their native receipt owner."""
from uuid import uuid4


async def commit_program_pending_records(journal, *, attempt_id, expected_config, run_month,
                                         source_cursor, qualified_decisions=None):
    """Return ordered immutable ``(native unit, acknowledged receipt)`` pairs.

    Original intents, orders and OMS groups must remain in the owned journal.
    Unsupported source families fail closed; this function performs no broker I/O.
    The caller holds the concrete assembly's exclusive operation lock.
    """
    from .program_execution_journal import ProgramExecutionJournal
    from src.trading_runtime.arte_journal_projection import project_program_journal_record
    from src.trading_runtime.rule_decision_basis_projection import RuleDecisionBasisBatch
    from src.trading_runtime.rule_decision_publication import QualifiedRuleDecisionBatch
    if type(journal) is not ProgramExecutionJournal:
        raise TypeError('Pending publication requires its concrete native program journal')
    journal.require_current()
    if type(source_cursor) is not str or not source_cursor:
        raise ValueError('Program publication requires its actual owned source cursor')
    registration = journal.writer._live_v4_lease.registration
    config = expected_config.get('strategy', expected_config) if isinstance(expected_config, dict) else None
    if not isinstance(config, dict) or (config.get('strategy_id'), config.get('strategy_revision')) != registration.key:
        raise ValueError('Pending publication differs from the installed program identity')
    qualifications = dict(qualified_decisions or {})
    records = tuple(journal.unfenced_records(after_sequence=journal.fenced_sequence))
    if len(records) > journal.max_pending_records:
        raise ValueError('Pending publication exceeds the owned staging bound')
    # Reject missing completion authority before publishing any preceding facts.
    for record in records:
        if record.payload.get('signal_type') == 'program_boundary_completed' and record.record_id not in qualifications:
            raise ValueError('Program completion lacks its concrete decision qualification')
    result = []
    for record in records:
        journal.require_current()
        fence = journal.fenced_receipt
        scope = dict(run_month=run_month, attempt_id=attempt_id,
            batch_id=str(uuid4()), prior_batch_id=fence.batch_id, source_cursor=source_cursor)
        kind = record.category, record.entity_type
        if kind == ('strategy', 'strategy_intent'):
            from src.trading_runtime.arte_intent_projection import strategy_intent_batch
            original = journal.program_intent_for_record(record.record_id)
            if original is None:
                raise ValueError('Program publication lost its original semantic intent')
            unit = strategy_intent_batch(original[0], run_id=record.run_id, account_id=record.account_id,
                sequence=record.sequence, run_status='running', recorded_at=record.recorded_at,
                record_id=record.record_id, correlation_id=str(record.payload.get('correlation_id') or ''),
                causation_id=str(record.payload.get('causation_id') or ''), **scope)
        elif kind == ('order_management', 'order_group_state'):
            from src.trading_runtime.arte_oms_projection import oms_group_state_batch
            group = journal.oms_group_for_record(record.record_id)
            admission = journal.oms_admission_for_record(record.record_id)
            if group is None or admission is None:
                raise ValueError('Program OMS publication lacks its actual admitted frozen group')
            source = journal.program_intent_source(group.intent.intent_id, record.account_id)
            unit = oms_group_state_batch(group, run_id=record.run_id, sequence=record.sequence,
                run_status='running', strategy_id=registration.strategy_id,
                strategy_revision=registration.revision, recorded_at=record.recorded_at,
                published_intent_batch=source.batch, committed_intent_batch_id=source.batch_id,
                admission_source_intent=source.intent, admission_reservation=admission,
                authorized_protection=journal.oms_effective_protection_for_record(record),
                journal_record_id=record.record_id, correlation_id=record.payload.get('correlation_id', ''),
                causation_id=record.payload.get('causation_id', ''), exact_prices=True, **scope)
        elif kind in {('command', 'order'), ('order_management', 'order_command')}:
            unit = _order(journal, record, registration, scope)
        elif kind in {('command', 'order_cancel'), ('broker', 'order_cancel_requested')}:
            from src.trading_runtime.arte_order_cancel_v4 import order_cancel_batch_v4
            unit = order_cancel_batch_v4(record, program_registration=registration, **scope)
        elif kind == ('command', 'order_modify'):
            from src.trading_runtime.arte_order_modify_command_v1 import order_modify_command_batch_v4
            request = journal.program_order_modify_for_record(record.record_id)
            if request is None:
                raise ValueError('Program amendment lost its actual replacement request')
            unit = order_modify_command_batch_v4(record, request, program_registration=registration, **scope)
        elif kind == ('protection', 'protection_change'):
            from src.trading_runtime.arte_protection_change_v4 import protection_change_batch_v4
            unit = protection_change_batch_v4(record, **scope)
        elif kind == ('broker', 'order_acknowledgement'):
            from src.trading_runtime.arte_broker_acknowledgement_v4 import broker_acknowledgement_batch_v4
            unit = broker_acknowledgement_batch_v4(record, **scope)
        elif record.category == 'broker_policy' and record.entity_type in {'order_warning_decision', 'order_reply_suppression'}:
            from src.trading_runtime.arte_broker_policy_v4 import broker_policy_batch_v4
            unit = broker_policy_batch_v4(record, **scope)
        else:
            lineage = {}
            for source in journal._program_oms_sources.values():
                for key, value in source.lineage.items():
                    if key in lineage and lineage[key] != value:
                        raise ValueError('Program committed OMS lineage is ambiguous')
                    lineage[key] = value
            unit = project_program_journal_record(record, journal=journal,
                expected_config=expected_config, expected_mode=journal.mode,
                exact_order_prices=True, exact_execution_prices=True, exact_signal_prices=True,
                committed_order_lineage=lineage, **scope)
        if record.payload.get('signal_type') == 'program_boundary_completed':
            unit = QualifiedRuleDecisionBatch(
                RuleDecisionBasisBatch.from_pending_completion(journal, record, unit),
                qualifications[record.record_id])
        receipt = await journal.commit_native(unit)
        result.append((unit, receipt))
    return tuple(result)


def _order(journal, record, registration, scope):
    from src.trading_runtime.arte_journal_projection import order_command_batch
    request = journal.order_request_for_record(record.record_id)
    if request is None:
        raise ValueError('Program command lost its actual typed broker request')
    payload = record.payload
    source = journal.program_intent_source(payload.get('strategy_intent_id'), record.account_id)
    oms = journal.program_oms_source(payload.get('order_group_id'), record.account_id)
    version = payload.get('policy_version')
    if type(version) is int and 0 <= version <= 0xFFFFFFFF:
        version = str(version)
    return order_command_batch(request, run_id=record.run_id, sequence=record.sequence,
        run_status='running', command_id=record.entity_id, created_at=record.event_time,
        recorded_at=record.recorded_at, strategy_id=registration.strategy_id,
        strategy_revision=registration.revision, strategy_intent_id=source.intent.intent_id,
        order_group_id=payload.get('order_group_id'), policy_version=version,
        strategy_intent_record_id=source.record_id, strategy_intent_content_hash=source.content_hash,
        source_intent=source.intent, source_intent_batch_id=source.batch_id,
        approved_oms_lineage=oms.lineage.get(request.cOID), v4_lineage_oms_record_id=oms.record_id,
        emit_v4_lineage=True, exact_prices=True, record_id=record.record_id,
        event_category=record.category, event_entity_type=record.entity_type,
        correlation_id=str(payload.get('correlation_id') or ''),
        causation_id=str(payload.get('causation_id') or ''), program_registration=registration, **scope)
