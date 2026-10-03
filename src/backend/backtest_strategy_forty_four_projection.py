"""Bounded Strategy 44 projection into the native closed V4 command graph."""
from __future__ import annotations

from uuid import UUID, NAMESPACE_URL, uuid5

from .backtest_strategy_forty_four_journal import StrategyFortyFourJournal
from .backtest_typed_projection import (
    project_pending_backtest_v4_prefix, committed_oms_order_lineage,
)
from src.trading_runtime.arte_journal_projection import order_command_batch
from src.trading_runtime.arte_journal_writer import TypedJournalBatch, _sealed_families
from src.trading_runtime.strategy_forty_four_rules import STRATEGY_ID, STRATEGY_NUMBER


def project_prefix(journal, *, attempt_id, run_month, prior_sequence,
                   prior_batch_id, source_cursor, expected_config,
                   through_sequence, published_sources=None,
                   committed_order_lineage=None, committed_order_lineage_proofs=None,
                   committed_order_lineage_oms_records=None,
                   fixed_market_parent_plan=None, fixed_market_execution_plan=None,
                   expected_market_start=None):
    if (type(journal) is not StrategyFortyFourJournal
            or expected_config.get("strategy_id") != STRATEGY_ID
            or type(expected_config.get("strategy_revision")) is not int
            or expected_config["strategy_revision"] != STRATEGY_NUMBER
            or type(prior_sequence) is not int or type(through_sequence) is not int
            or not 0 < through_sequence - prior_sequence <= 512):
        raise ValueError("Strategy 44 projection needs its exact bounded native run")
    attempt, previous = str(UUID(attempt_id)), str(UUID(prior_batch_id))
    sources = dict(published_sources or {})
    lineages = dict(committed_order_lineage or {})
    proofs = dict(committed_order_lineage_proofs or {})
    oms_records = dict(committed_order_lineage_oms_records or {})
    records = journal.unfenced_records(after_sequence=prior_sequence,
                                       through_sequence=through_sequence)
    if len(records) != through_sequence - prior_sequence:
        raise ValueError("Strategy 44 projection prefix is not contiguous")
    units = []
    cursor = source_cursor
    for sequence, record in enumerate(records, prior_sequence + 1):
        if record.run_id != journal.run_id or record.sequence != sequence:
            raise ValueError("Strategy 44 journal changed its run or causal order")
        batch_id = str(uuid5(NAMESPACE_URL,
            f"arte-backtest-v1:{journal.run_id}:{attempt}:{sequence}:{record.record_id}"))
        if (record.category, record.entity_type) == ("command", "order"):
            request = journal.order_request_for_record(record.record_id)
            payload = record.payload
            source = sources.get(str(payload.get("strategy_intent_id") or ""))
            if (request is None or source is None or len(source[0].intents) != 1
                    or source[0].first_sequence >= sequence
                    or record.entity_id != request.cOID or record.account_id != request.acctId
                    or payload.get("strategy_id") != STRATEGY_ID
                    or payload.get("strategy_revision") != STRATEGY_NUMBER
                    or payload.get("intent_id") != source[1].intent_id
                    or payload.get("ticker") != request.ticker
                    or any(payload.get(key) != value for key, value in request.to_cpapi().items())):
                raise RuntimeError("Strategy 44 command lacks its exact native source")
            sealed, = dict(_sealed_families(source[0]))["trading_strategy_intent_v1"]
            policy = payload.get("policy_version")
            if type(policy) is int:
                if not 0 <= policy <= 0xFFFFFFFF:
                    raise ValueError("Strategy 44 command policy revision is invalid")
                policy = str(policy)
            unit = order_command_batch(request, run_id=record.run_id, run_month=run_month,
                attempt_id=attempt, batch_id=batch_id, prior_batch_id=previous,
                sequence=sequence, source_cursor=cursor, run_status="running",
                command_id=record.entity_id, created_at=record.event_time,
                recorded_at=record.recorded_at, strategy_id=STRATEGY_ID,
                strategy_revision=STRATEGY_NUMBER, strategy_intent_id=source[1].intent_id,
                order_group_id=str(payload.get("order_group_id") or ""), policy_version=policy,
                strategy_intent_record_id=source[0].intents[0]["record_id"],
                strategy_intent_content_hash=sealed["content_hash"], source_intent=source[1],
                source_intent_batch_id=source[0].batch_id,
                approved_oms_lineage=lineages.get(request.cOID),
                v4_lineage_proof_record_id=proofs.get(request.cOID),
                v4_lineage_oms_record_id=oms_records.get(request.cOID), emit_v4_lineage=True,
                record_id=record.record_id, event_category=record.category,
                event_entity_type=record.entity_type,
                correlation_id=str(payload.get("correlation_id") or ""),
                causation_id=str(payload.get("causation_id") or ""))
            projected = (unit,)
        else:
            projected = project_pending_backtest_v4_prefix(journal, attempt_id=attempt,
                run_month=run_month, prior_sequence=sequence - 1, prior_batch_id=previous,
                source_cursor=cursor, expected_config=expected_config,
                fixed_market_parent_plan=fixed_market_parent_plan,
                fixed_market_execution_plan=fixed_market_execution_plan,
                expected_market_start=expected_market_start,
                published_sources=sources, committed_order_lineage=lineages,
                committed_order_lineage_proofs=proofs,
                committed_order_lineage_oms_records=oms_records, through_sequence=sequence)
        if len(projected) != 1:
            raise RuntimeError("Strategy 44 logical event changed its exclusive native batch")
        unit = projected[0]
        base = unit if type(unit) is TypedJournalBatch else unit.base
        if (base.first_sequence != sequence or base.last_sequence != sequence
                or base.batch_id != batch_id or base.prior_batch_id != previous or len(base.events) != 1):
            raise RuntimeError("Strategy 44 projection changed its closed causal prefix")
        intent = journal.strategy_one_protection_for_record(record.record_id)
        if intent is not None:
            sources[intent.intent_id] = (base, intent)
        if (record.category, record.entity_type) == ("order_management", "order_group_state"):
            group = journal.oms_group_for_record(record.record_id)
            additions = committed_oms_order_lineage(group, run_id=record.run_id,
                strategy_id=STRATEGY_ID, strategy_revision=STRATEGY_NUMBER,
                authorized_protection=journal.oms_effective_protection_for_record(record))
            for key, lineage in additions.items():
                if key in lineages and lineages[key] != lineage:
                    raise RuntimeError("Strategy 44 frozen target or native lineage changed")
                lineages[key] = lineage
                oms_records[key] = str(UUID(record.record_id))
        units.append(unit)
        previous, cursor = base.batch_id, base.source_cursor
    return tuple(units)
