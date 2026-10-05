"""Read-only, all-or-nothing projection of a fixed Backtest journal prefix.

This module prepares normalized arte batches. It does not submit or fence them;
the fixed Backtest launch remains blocked until typed recovery is complete.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Mapping
from uuid import UUID, NAMESPACE_URL, uuid5

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.arte_journal_projection import project_journal_record
from src.trading_runtime.arte_journal_writer import TypedJournalBatch
from src.trading_runtime.arte_profit_giveback_v4 import V4ProfitGivebackBatch
from src.trading_runtime.strategy_profit_giveback_exit import profit_giveback_reason
from src.trading_runtime.arte_confirmed_ah_failure_v4 import V4ConfirmedAhFailureBatch
from src.trading_runtime.strategy_liquidity_fade_transport import V4LiquidityFadeFailureBatch
from src.trading_runtime.strategy_liquidity_fade_exit import liquidity_fade_reason
from src.trading_runtime.arte_journal_writer import V3SqueezeBatch
from src.trading_runtime.arte_oms_tactic_projection import (
    V4OmsTacticBatch, tactic_rows,
)
from src.trading_runtime.arte_journal_writer import (
    V4BrokerAcknowledgementBatch, V4OrderCancelBatch, V4OrderRepriceBatch,
    V4ProtectionChangeBatch,
    V4StrategyOneEntryBatch, _coalesce_unpublished,
)
from src.trading_runtime.arte_protection_reconciliation_v4 import (
    V4ProtectionReconciliationBatch,
)
from src.trading_runtime.arte_risk_action_v4 import V4RiskActionBatch
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.arte_portfolio_allocation_v4 import (
    V4PortfolioAllocationBatch, project_portfolio_allocation_v3,
)
from src.trading_runtime.arte_reservation_reason_v4 import (
    V4ReservationReasonBatch, project_reservation_reasons_v3,
)


NIL_BATCH_ID = str(UUID(int=0))


def committed_oms_order_lineage(group: object, *, run_id: str,
                                strategy_id: str, strategy_revision: int,
                                authorized_protection: Mapping | None = None) -> dict[str, tuple]:
    """Index only lineage already validated against a typed OMS transition."""
    from src.trading_runtime.arte_oms_projection import canonical_oms_order_metadata

    result = {}
    for order in group.orders:
        if not order.cOID:
            raise ValueError("Typed OMS order lacks a client order ID")
        expected = {
            "strategy_id": strategy_id,
            "canonical_strategy_revision": strategy_revision,
            "canonical_run_id": run_id,
            "canonical_metadata": canonical_oms_order_metadata(
                group, order, authorized_protection),
        }
        lineage = (expected, group.account_id, order.ticker.upper(), order.conid,
                   group.group_id, group.intent.intent_id)
        prior = result.setdefault(order.cOID, lineage)
        if prior != lineage:
            raise ValueError("Client order ID has conflicting OMS lineage")
    return result


def authorized_oms_lineage_transition(
    old: tuple, new: tuple, *, client_order_id: str,
    proof: object | None,
) -> bool:
    """Permit only a target child's journaled scalar amendment delta."""
    if (proof is None or len(old) != len(new) or len(old) not in {4, 6}
            or old[1:] != new[1:]
            or not isinstance(old[0], dict) or not isinstance(new[0], dict)
            or not isinstance(old[0].get("canonical_metadata"), dict)
            or not isinstance(new[0].get("canonical_metadata"), dict)):
        return False
    before, after = old[0], new[0]
    old_meta, new_meta = before["canonical_metadata"], after["canonical_metadata"]
    amended = {"reason", "replacement_intent_id", "target_price"}
    if ({key: value for key, value in before.items()
         if key != "canonical_metadata"}
            != {key: value for key, value in after.items()
                if key != "canonical_metadata"}
            or {key: value for key, value in old_meta.items()
                if key not in amended}
            != {key: value for key, value in new_meta.items()
                if key not in amended}):
        return False
    payload = getattr(proof, "payload", {})
    return bool(
        getattr(proof, "category", None) == "protection"
        and getattr(proof, "entity_type", None) == "protection_change"
        and payload.get("phase") == "effective"
        and payload.get("kind") == "target"
        and payload.get("action") == "replace_profit_target"
        and payload.get("client_order_id") == client_order_id
        and payload.get("price") == new_meta.get("target_price")
        and payload.get("intent_id") == new_meta.get("replacement_intent_id")
        and new_meta.get("reason") == "structural_profit_target_advanced")


@dataclass(frozen=True, slots=True)
class ProjectedBacktestPrefix:
    batches: tuple[TypedJournalBatch, ...]
    last_sequence: int
    last_batch_id: str
    source_cursor: str


def project_pending_backtest_v4_prefix(
    journal: BacktestMemoryJournal, *, attempt_id: str, run_month: date,
    prior_sequence: int, prior_batch_id: str = NIL_BATCH_ID,
    source_cursor: str = "start", expected_config: dict | None = None,
    fixed_market_parent_plan: object | None = None,
    fixed_market_execution_plan: object | None = None,
    first_price_source: object | None = None,
    expected_market_start: datetime | None = None,
    published_sources: Mapping[str, tuple[TypedJournalBatch, object]] | None = None,
    committed_order_lineage: Mapping[str, tuple] | None = None,
    committed_order_lineage_proofs: Mapping[str, str] | None = None,
    committed_order_lineage_oms_records: Mapping[str, str] | None = None,
    through_sequence: int,
) -> tuple[TypedJournalBatch | V4StrategyOneEntryBatch
           | V4ProfitGivebackBatch
           | V4ConfirmedAhFailureBatch
           | V4OmsTacticBatch
           | V4BrokerAcknowledgementBatch | V4OrderCancelBatch
           | V4OrderRepriceBatch | V4RiskActionBatch | V4ProtectionChangeBatch
           | V4ProtectionReconciliationBatch | V4PortfolioAllocationBatch
           | V4ReservationReasonBatch, ...]:
    """Project one bounded V4 prefix; special families never enter a base batch."""
    from src.trading_runtime.arte_broker_acknowledgement_v4 import (
        broker_acknowledgement_batch_v4,
    )
    from src.trading_runtime.arte_protection_change_v4 import (
        protection_change_batch_v4,
    )
    from src.trading_runtime.arte_protection_reconciliation_v4 import (
        protection_reconciliation_batch_v4,
    )
    from src.trading_runtime.arte_strategy_one_entry_journal import (
        project_strategy_one_entry_evidence,
    )
    from src.trading_runtime.arte_strategy_one_add_journal import (
        project_strategy_one_add_evidence,
    )
    from src.trading_runtime.strategy_one_intent import (
        strategy_one_add_intent, strategy_one_entry_intent,
    )
    from src.trading_runtime.arte_oms_projection import oms_group_state_batch

    attempt = str(UUID(attempt_id))
    if first_price_source is not None:
        from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority
        if (type(first_price_source) is not CertifiedPriceReadbackAuthority
                or first_price_source.run_id != journal.run_id):
            raise ValueError("V4 projection price source differs from its run")
    previous = str(UUID(prior_batch_id))
    if (run_month.day != 1 or type(prior_sequence) is not int
            or prior_sequence < 0 or not source_cursor
            or (prior_sequence == 0) != (previous == NIL_BATCH_ID)
            or type(through_sequence) is not int
            or not prior_sequence < through_sequence
                   <= journal.latest_sequence(journal.run_id)):
        raise ValueError("V4 projection prefix identity is invalid")
    records = journal.unfenced_records(
        after_sequence=prior_sequence, through_sequence=through_sequence)
    if len(records) != through_sequence - prior_sequence:
        raise ValueError("V4 projection prefix is not contiguous")
    units = []
    ordinary: list[TypedJournalBatch] = []
    sources = dict(published_sources or {})
    order_lineage = dict(committed_order_lineage or {})
    order_lineage_proofs = dict(committed_order_lineage_proofs or {})
    order_lineage_oms_records = dict(committed_order_lineage_oms_records or {})
    cursor = source_cursor
    for sequence, record in enumerate(records, start=prior_sequence + 1):
        if record.run_id != journal.run_id or record.sequence != sequence:
            raise ValueError("V4 projection changed its run or event sequence")
        canonical_json(record.payload)
        profit_source = journal.profit_giveback_exit_for_record(record.record_id)
        batch_id = str(uuid5(NAMESPACE_URL,
            f"arte-backtest-v1:{record.run_id}:{attempt}:{sequence}:{record.record_id}"))
        kind = (record.category, record.entity_type)
        confirmation_source = (journal.confirmed_ah_exit_for_record(record.record_id)
                               if kind == ('strategy', 'strategy_intent') else None)
        liquidity_source = (journal.liquidity_fade_exit_for_record(record.record_id)
                            if kind == ('strategy', 'strategy_intent') else None)
        if kind == ("checkpoint", "market_boundary"):
            cursor = record.entity_id
        from src.trading_runtime.numbered_fixed_strategy import is_numbered_fixed_strategy
        if (kind == ("command", "order")
                and is_numbered_fixed_strategy((expected_config or {}).get('strategy_id'),
                    (expected_config or {}).get('strategy_revision'))):
            from src.trading_runtime.arte_journal_projection import order_command_batch
            from src.trading_runtime.arte_journal_writer import _sealed_families

            request = journal.order_request_for_record(record.record_id)
            payload = record.payload
            source = sources.get(str(payload.get("strategy_intent_id") or ""))
            if (request is None or source is None
                    or not source[0].intents or source[0].first_sequence >= sequence
                    or record.entity_id != request.cOID
                    or record.account_id != request.acctId
                    or payload.get("strategy_id") != "early-squeeze-strategy"
                    or payload.get("strategy_revision") != expected_config["strategy_revision"]
                    or payload.get("intent_id") != source[1].intent_id
                    or payload.get("ticker") != request.ticker
                    or any(payload.get(key) != value
                           for key, value in request.to_cpapi().items())):
                raise RuntimeError("Strategy 1 command lacks its exact typed source")
            policy_version = payload.get("policy_version")
            if type(policy_version) is int:
                if not 0 <= policy_version <= 0xFFFFFFFF:
                    raise ValueError("Strategy 1 command policy revision is invalid")
                policy_version = str(policy_version)
            sealed_source = dict(_sealed_families(source[0]))[
                "trading_strategy_intent_v1"]
            if len(sealed_source) != 1:
                raise RuntimeError("Strategy 1 command source is not one typed intent")
            unit = order_command_batch(
                request, run_id=record.run_id, run_month=run_month,
                attempt_id=attempt, batch_id=batch_id,
                prior_batch_id=previous, sequence=sequence,
                source_cursor=cursor, run_status="running",
                command_id=record.entity_id, created_at=record.event_time,
                recorded_at=record.recorded_at,
                strategy_id="early-squeeze-strategy", strategy_revision=expected_config["strategy_revision"],
                strategy_intent_id=source[1].intent_id,
                order_group_id=str(payload.get("order_group_id") or ""),
                policy_version=policy_version,
                strategy_intent_record_id=source[0].intents[0]["record_id"],
                strategy_intent_content_hash=sealed_source[0]["content_hash"],
                source_intent=source[1], source_intent_batch_id=source[0].batch_id,
                approved_oms_lineage=order_lineage.get(request.cOID),
                v4_lineage_proof_record_id=order_lineage_proofs.get(request.cOID),
                v4_lineage_oms_record_id=order_lineage_oms_records.get(request.cOID),
                emit_v4_lineage=True,
                record_id=record.record_id, event_category=record.category,
                event_entity_type=record.entity_type,
                correlation_id=str(payload.get("correlation_id") or ""),
                causation_id=str(payload.get("causation_id") or ""),
            )
        elif kind in {("risk", "kill_entry_order"),
                    ("risk", "emergency_flatten")}:
            from src.trading_runtime.arte_risk_action_v4 import risk_action_batch_v4
            unit = risk_action_batch_v4(
                record, run_month=run_month, attempt_id=attempt,
                batch_id=batch_id, prior_batch_id=previous,
                source_cursor=cursor)
        elif kind in {("broker", "order_repriced"),
                    ("broker", "order_reprice_error")}:
            from src.trading_runtime.arte_order_reprice_v4 import order_reprice_batch_v4
            unit = order_reprice_batch_v4(
                record, run_month=run_month, attempt_id=attempt,
                batch_id=batch_id, prior_batch_id=previous,
                source_cursor=cursor)
        elif kind in {("command", "order_cancel"),
                    ("broker", "order_cancel_requested")}:
            from src.trading_runtime.arte_order_cancel_v4 import order_cancel_batch_v4
            unit = order_cancel_batch_v4(
                record, run_month=run_month, attempt_id=attempt,
                batch_id=batch_id, prior_batch_id=previous,
                source_cursor=cursor)
        elif kind == ("broker", "order_acknowledgement"):
            unit = broker_acknowledgement_batch_v4(
                record, run_month=run_month, attempt_id=attempt,
                batch_id=batch_id, prior_batch_id=previous,
                source_cursor=cursor)
        elif kind == ("order_management", "protection_replacement_deferred"):
            from src.trading_runtime.arte_protection_deferral_v4 import (
                protection_deferral_batch_v4,
            )
            source = sources.get(record.entity_id)
            if source is None:
                raise RuntimeError("Protection deferral lacks a journaled source intent")
            unit = protection_deferral_batch_v4(
                record, source_batch=source[0], source_intent=source[1],
                run_month=run_month, attempt_id=attempt, batch_id=batch_id,
                prior_batch_id=previous, source_cursor=cursor,
                strategy_id=(expected_config or {}).get("strategy_id"),
                strategy_revision=(expected_config or {}).get("strategy_revision"))
        elif kind == ("protection", "protection_change"):
            unit = protection_change_batch_v4(
                record, run_month=run_month, attempt_id=attempt,
                batch_id=batch_id, prior_batch_id=previous,
                source_cursor=cursor)
        elif kind == ("order_management", "protection_reconciliation"):
            unit = protection_reconciliation_batch_v4(
                record, run_month=run_month, attempt_id=attempt,
                batch_id=batch_id, prior_batch_id=previous,
                source_cursor=cursor)
        elif kind == ("order_management", "order_group_state"):
            group = journal.oms_group_for_record(record.record_id)
            admission = journal.oms_admission_for_record(record.record_id)
            if group is None or admission is None:
                raise RuntimeError("V4 OMS transition lacks its immutable admission")
            if (record.entity_id != group.group_id
                    or record.account_id != group.account_id
                    or record.payload.get("intent_id") != group.intent.intent_id
                    or record.payload.get("state") != group.state.value
                    or record.payload.get("action") != group.intent.action
                    or record.payload.get("ticker") != group.intent.ticker):
                raise RuntimeError("V4 OMS transition differs from its frozen group")
            source = sources.get(group.intent.intent_id)
            if source is None:
                raise RuntimeError("V4 OMS transition lacks its committed source intent")
            source_batch, source_intent = source
            protection_proof = journal.oms_effective_protection_for_record(record)
            unit = oms_group_state_batch(
                group, run_id=record.run_id, run_month=run_month,
                attempt_id=attempt, batch_id=batch_id,
                prior_batch_id=previous, sequence=sequence,
                source_cursor=cursor, run_status="running",
                strategy_id=record.payload["strategy_id"],
                strategy_revision=record.payload["strategy_revision"],
                recorded_at=record.recorded_at,
                published_intent_batch=source_batch,
                committed_intent_batch_id=source_batch.batch_id,
                admission_source_intent=source_intent,
                admission_reservation=admission,
                authorized_protection=protection_proof,
                journal_record_id=record.record_id,
                correlation_id=record.payload.get("correlation_id", ""),
                causation_id=record.payload.get("causation_id", ""))
            if (unit.events[0]["event_time"] != record.event_time.isoformat()
                    or unit.events[0]["entity_id"] != record.entity_id
                    or unit.events[0]["account_id"] != record.account_id):
                raise RuntimeError("V4 OMS typed event differs from its journal source")
            tactic_state, tactic_steps = tactic_rows(
                group.tactic, group_record_id=record.record_id,
                run_id=record.run_id,
                event_month=record.event_time.astimezone(timezone.utc).strftime("%Y-%m-01"),
                batch_id=batch_id, account_id=group.account_id)
            unit = V4OmsTacticBatch(unit, tactic_state, tactic_steps)
            for client_order_id, lineage in committed_oms_order_lineage(
                    group, run_id=record.run_id,
                    strategy_id=record.payload["strategy_id"],
                    strategy_revision=record.payload["strategy_revision"],
                    authorized_protection=protection_proof).items():
                if (client_order_id in order_lineage
                        and order_lineage[client_order_id] != lineage
                        and not authorized_oms_lineage_transition(
                            order_lineage[client_order_id], lineage,
                            client_order_id=client_order_id,
                            proof=protection_proof.get(f"target:{client_order_id}"))):
                    raise RuntimeError("OMS changed committed order lineage without typed amendment")
                order_lineage[client_order_id] = lineage
                order_lineage_oms_records[client_order_id] = str(UUID(record.record_id))
                proof = protection_proof.get(f"target:{client_order_id}")
                if (lineage[0]["canonical_metadata"].get("reason") ==
                        "structural_profit_target_advanced"):
                    if proof is None:
                        raise RuntimeError("Amended OMS lineage lacks its typed proof")
                    order_lineage_proofs[client_order_id] = str(UUID(proof.record_id))
                else:
                    order_lineage_proofs.pop(client_order_id, None)
        elif kind == ("portfolio_management", "portfolio_allocation"):
            projected = project_portfolio_allocation_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (projected.event,))
            unit = V4PortfolioAllocationBatch(base, projected.detail)
        elif (kind == ("portfolio_management", "portfolio_reservation")
              and record.payload.get("event") in {
                  "reservation_released", "entry_reprice_authorized"}):
            base = project_journal_record(
                record, run_month=run_month, attempt_id=attempt,
                batch_id=batch_id, prior_batch_id=previous,
                source_cursor=cursor, expected_config=expected_config,
                expected_mode="backtest", allow_v3_reservation_reasons=True)
            reasons = project_reservation_reasons_v3(record, batch_id=batch_id)
            unit = V4ReservationReasonBatch(base, reasons) if reasons else base
        else:
            progress_state = (journal.backtest_progress_for_record(record.record_id)
                              if kind == ("checkpoint", "market_boundary") else None)
            batch = project_journal_record(
                record, run_month=run_month, attempt_id=attempt,
                batch_id=batch_id, prior_batch_id=previous,
                source_cursor=cursor, expected_config=expected_config,
                expected_mode="backtest",
                fixed_market_parent_plan=fixed_market_parent_plan,
                fixed_market_execution_plan=fixed_market_execution_plan,
                expected_market_start=expected_market_start,
                committed_order_lineage=order_lineage)
            if progress_state is not None:
                from src.backend.typed_backtest_progress import project_backtest_progress
                typed_progress = project_backtest_progress(
                    record, progress_state, run_month=run_month,
                    attempt_id=attempt, batch_id=batch_id,
                    prior_batch_id=previous)
                if (typed_progress.events != batch.events
                        or typed_progress.backtest_cursors != batch.backtest_cursors):
                    raise RuntimeError("Backtest progress differs from fixed cursor contract")
                batch = replace(batch, backtest_progress=typed_progress.backtest_progress)
            sidecar = journal.strategy_one_entry_for_record(record.record_id)
            automatic_source = journal.automatic_entry_for_record(record.record_id)
            add_sidecar = journal.strategy_one_add_for_record(record.record_id)
            protection_source = journal.strategy_one_protection_for_record(
                record.record_id)
            failure_source = journal.followthrough_exit_for_record(record.record_id)
            session_exit_source = journal.numbered_session_exit_for_record(record.record_id)
            if session_exit_source is not None:
                if protection_source is not None:
                    raise RuntimeError("Session exit has conflicting source authorities")
                protection_source = session_exit_source
            if sum(value is not None for value in (
                    automatic_source, sidecar, add_sidecar, protection_source, failure_source, profit_source, confirmation_source, liquidity_source)) > 1:
                raise RuntimeError("Strategy 1 intent has two source authorities")
            if (kind == ("strategy", "strategy_intent")
                    and record.payload.get("reason") == "strategy_nine_followthrough_failure"
                    and failure_source is None):
                raise RuntimeError("Follow-through intent lacks its normalized witness")
            if (kind == ('strategy', 'strategy_intent')
                    and record.payload.get('reason') in {
                        profit_giveback_reason(number) for number in (31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52)}
                    and profit_source is None):
                raise RuntimeError('Profit intent lacks its normalized witness')
            if (kind == ('strategy', 'strategy_intent')
                    and record.payload.get('reason') in ('strategy_thirty_four_confirmed_ah_failure',
                                                        'strategy_thirty_five_confirmed_ah_failure',
                                                        'strategy_thirty_six_confirmed_ah_failure',
                                                        'strategy_thirty_seven_confirmed_ah_failure',
                                                        'strategy_thirty_eight_confirmed_ah_failure')
                    and confirmation_source is None):
                raise RuntimeError('AH confirmation intent lacks its normalized witness')
            if (kind == ('strategy', 'strategy_intent') and record.payload.get('reason') in {
                    liquidity_fade_reason(number) for number in (35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52)}
                    and liquidity_source is None):
                raise RuntimeError('Liquidity intent lacks its normalized witness')
            if sidecar is None and add_sidecar is None:
                if kind == ('strategy', 'strategy_intent') and record.payload.get('reason') == 'prepared_ladder_entry':
                    if automatic_source is None:
                        raise RuntimeError('Automatic ladder parent lacks its normalized source companion')
                if (kind == ("strategy", "strategy_intent")
                        and record.payload.get("reason") in {
                            "strategy_one_entry", "strategy_one_add"}):
                    raise RuntimeError("Strategy 1 journal intent lacks normalized evidence")
                if (kind == ("strategy", "strategy_intent")
                        and record.payload.get("strategy_id") == "early-squeeze-strategy"
                        and record.payload.get("strategy_revision") in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52)
                        and record.payload.get("action") in {
                            "replace_protective_stop", "replace_profit_target"}
                        and protection_source is None):
                    raise RuntimeError("Strategy 1 protection intent lacks typed source")
                unit = batch
                if automatic_source is not None:
                    from src.trading_runtime.automatic_ladder_transport import V4AutomaticLadderBatch
                    if (kind != ('strategy', 'strategy_intent')
                            or record.entity_id != automatic_source.intent.intent_id
                            or record.account_id == ''):
                        raise RuntimeError('Automatic ladder companion differs from its exact parent')
                    unit = V4AutomaticLadderBatch.from_request(batch, automatic_source)
                    sources[automatic_source.intent.intent_id] = (batch, automatic_source.intent)
                if protection_source is not None:
                    if (kind != ("strategy", "strategy_intent")
                            or record.entity_id != protection_source.intent_id
                            or record.account_id == ""
                            or protection_source.payload() != {
                                key: value for key, value in record.payload.items()
                                if key not in {"strategy_id", "strategy_revision",
                                               "correlation_id", "causation_id"}}):
                        raise RuntimeError("Strategy 1 protection source differs from typed intent")
                    prior_source = sources.get(protection_source.intent_id)
                    if prior_source is not None and prior_source != (
                            batch, protection_source):
                        raise RuntimeError("V4 Strategy 1 protection identity was reused")
                    sources[protection_source.intent_id] = (batch, protection_source)
            elif sidecar is not None:
                proposal, session_date = sidecar
                price_rows, price_authorities = (), ()
                if proposal.strategy_number in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52):
                    from src.backend.backtest_strategy_certified_price_break import (
                        certified_price_entry_intent, project_certified_price_entry,
                    )
                    if first_price_source is None:
                        raise ValueError("Strategy20 V4 projection lacks its native price source")
                    if proposal.strategy_number in (37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52):
                        from .backtest_strategy_episode_activity_source import certified_episode_entry_intent
                        intent = certified_episode_entry_intent(first_price_source, proposal, session_date=session_date)
                    else:
                        intent = certified_price_entry_intent(first_price_source.plan,
                            proposal, session_date=session_date)
                    price_packet = project_certified_price_entry(first_price_source.plan,
                        proposal, run_id=batch.run_id, batch_id=batch.batch_id,
                        parent_record_id=record.record_id,
                        event_month=batch.intents[0]["event_month"])
                    price_rows, price_authorities = price_packet.rows, (price_packet.authority,)
                else:
                    intent = strategy_one_entry_intent(proposal, session_date=session_date)
                evidence = project_strategy_one_entry_evidence(
                    proposal, intent, session_date=session_date,
                    run_id=batch.run_id, batch_id=batch.batch_id,
                    parent_record_id=record.record_id, first_price_source=first_price_source)
                from src.trading_runtime.arte_rising_momentum_entry_v4 import project_rising_momentum_entry
                momentum = project_rising_momentum_entry(proposal, run_id=batch.run_id,
                    batch_id=batch.batch_id, parent_record_id=record.record_id,
                    event_month=evidence["event_month"])
                from src.trading_runtime.arte_initial_momentum_entry_v4 import project_initial_momentum_entry
                initial = project_initial_momentum_entry(proposal, proposal.initial_momentum,
                    run_id=batch.run_id, batch_id=batch.batch_id,
                    parent_record_id=record.record_id, event_month=evidence["event_month"])
                activity_rows = ()
                if proposal.strategy_number in (36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52):
                    from src.trading_runtime.arte_entry_activity_v4 import project_entry_activity
                    activity_source = getattr(first_price_source, 'entry_activity_source', None)
                    if activity_source is None:
                        raise ValueError('Strategy 36 V4 projection lacks certified activity source')
                    witness = (activity_source.witness(proposal.ticker, proposal.boundary_ms)
                               if proposal.strategy_number in (37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52) else
                               activity_source.plan.witness(proposal.ticker, proposal.boundary_ms))
                    activity_rows = (project_entry_activity(witness, run_id=batch.run_id,
                        batch_id=batch.batch_id, parent_record_id=record.record_id,
                        event_month=evidence['event_month'], strategy_number=proposal.strategy_number),)
                unit = V4StrategyOneEntryBatch(batch, (evidence,), momentum_evidence=momentum,
                    initial_momentum_evidence=initial, first_price_evidence=price_rows,
                    first_price_authorities=price_authorities,
                    entry_activity_evidence=activity_rows,
                    first_price_source=first_price_source if proposal.strategy_number in (36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52) else None)
                prior_source = sources.get(intent.intent_id)
                if prior_source is not None and prior_source != (batch, intent):
                    raise RuntimeError("V4 Strategy 1 intent identity was reused")
                sources[intent.intent_id] = (batch, intent)
            else:
                proposal, session_date = add_sidecar
                intent = strategy_one_add_intent(
                    proposal, session_date=session_date)
                payload = {key: value for key, value in record.payload.items()
                           if key not in {"strategy_id", "strategy_revision",
                                          "correlation_id", "causation_id"}}
                if (record.account_id != proposal.account_id
                        or record.entity_id != intent.intent_id
                        or canonical_json(payload) != canonical_json(intent.payload())
                        or len(batch.intents) != 1
                        or batch.intents[0]["intent_id"] != intent.intent_id):
                    raise RuntimeError("Strategy 1 add differs from its typed intent")
                evidence = project_strategy_one_add_evidence(
                    proposal, intent, session_date=session_date,
                    run_id=batch.run_id, batch_id=batch.batch_id,
                    parent_record_id=record.record_id)
                unit = V4StrategyOneEntryBatch(batch, (), (evidence,))
                prior_source = sources.get(intent.intent_id)
                if prior_source is not None and prior_source != (batch, intent):
                    raise RuntimeError("V4 Strategy 1 add identity was reused")
                sources[intent.intent_id] = (batch, intent)
        if journal.followthrough_exit_for_record(record.record_id) is not None:
            from src.trading_runtime.arte_followthrough_failure_v4 import (
                V4FollowThroughFailureBatch, project_followthrough_failure)
            intent, witness, source_entry_id = journal.followthrough_exit_for_record(record.record_id)
            source = sources.get(source_entry_id)
            if source is None:
                raise RuntimeError("Failure exit requires its exact original typed entry source")
            # Entry authority is retained by the publisher, including its child.
            entry = next((u for u in units if isinstance(u, V4StrategyOneEntryBatch)
                          and any(r['intent_id'] == source_entry_id for r in u.base.intents)), None)
            if entry is not None:
                assignment_id = entry.entry_evidence[0]['assignment_id']
            elif isinstance(source[0], V4StrategyOneEntryBatch) and source[0].entry_evidence:
                assignment_id = source[0].entry_evidence[0]['assignment_id']
            else:
                assignment_id = journal.assignment_for_intent(source_entry_id) if hasattr(journal, 'assignment_for_intent') else ''
            if not assignment_id:
                raise RuntimeError("Failure source has no normalized assignment evidence")
            failure = project_followthrough_failure(witness, intent, source_entry_id,
                run_id=batch.run_id, batch_id=batch.batch_id, parent_record_id=record.record_id,
                assignment_id=assignment_id, strategy_number=record.payload["strategy_revision"])
            unit = V4FollowThroughFailureBatch(batch, failure)
            sources[intent.intent_id] = (batch, intent)
        if profit_source is not None:
            from zoneinfo import ZoneInfo
            from src.trading_runtime.arte_profit_giveback_v4 import project_profit_giveback
            from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
            from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions
            intent, witness, source_entry_id, arm = profit_source
            payload = {key: value for key, value in record.payload.items()
                       if key not in {'strategy_id', 'strategy_revision', 'correlation_id', 'causation_id'}}
            if (record.entity_id != intent.intent_id
                    or record.account_id != arm.candidate.account_id
                    or canonical_json(payload) != canonical_json(intent.payload())):
                raise RuntimeError('Profit journal payload changed its immutable exit source')
            source = sources.get(source_entry_id)
            assignment = journal.assignment_for_intent(source_entry_id)
            if (source is None or source[0].run_id != record.run_id
                    or source[0].last_sequence >= arm.checkpoint_sequence
                    or len(source[0].intents) != 1 or len(source[0].events) != 1
                    or source[0].events[0]['account_id'] != record.account_id
                    or source[0].events[0]['entity_id'] != source_entry_id
                    or source[0].intents[0]['intent_id'] != source_entry_id
                    or source[0].intents[0]['account_id'] != record.account_id
                    or source[1].action != 'enter_long' or source[1].reason != 'strategy_one_entry'
                    or source[1].intent_id != source_entry_id or source[1].ticker != intent.ticker
                    or source[1].reference_price != witness.reference_ask
                    or source[1].invalidation_price != witness.initial_stop
                    or assignment != arm.candidate.assignment_id
                    or record.payload['strategy_revision'] not in (31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52)):
                raise RuntimeError('Profit exit requires its exact original typed entry source')
            financial = StrategyOneFinancialView(assignment, record.account_id, intent.ticker,
                AssignmentStatus.WATCHING, StrategyPermissions(), intent.quantity,
                False, False, False, 1)
            profit = project_profit_giveback(witness, intent, financial,
                session_date=intent.event_time.astimezone(ZoneInfo('America/New_York')).date(),
                source_entry_intent_id=source_entry_id, run_id=batch.run_id,
                batch_id=batch.batch_id, parent_record_id=record.record_id,
                source_manager_snapshot_id=arm.snapshot_id,
                source_manager_checkpoint_sequence=arm.checkpoint_sequence,
                strategy_number=record.payload['strategy_revision'])
            unit = V4ProfitGivebackBatch(batch, profit)
            sources[intent.intent_id] = (batch, intent)
        if confirmation_source is not None:
            from src.trading_runtime.arte_confirmed_ah_failure_v4 import project_confirmed_ah_failure
            intent, witness, financial, source_entry_id, session_date = confirmation_source
            payload = {key: value for key, value in record.payload.items()
                       if key not in {'strategy_id', 'strategy_revision', 'correlation_id', 'causation_id'}}
            source = sources.get(source_entry_id)
            if (record.entity_id != intent.intent_id or record.account_id != financial.account_id
                    or canonical_json(payload) != canonical_json(intent.payload())
                    or source is None or source[0].run_id != record.run_id
                    or source[0].last_sequence >= record.sequence
                    or len(source[0].intents) != 1 or len(source[0].events) != 1
                    or source[0].events[0]['account_id'] != record.account_id
                    or source[0].events[0]['entity_id'] != source_entry_id
                    or source[0].intents[0]['intent_id'] != source_entry_id
                    or source[1].intent_id != source_entry_id or source[1].ticker != intent.ticker
                    or source[1].action != 'enter_long' or source[1].reason != 'strategy_one_entry'
                    or source[1].reference_price != witness.five_second.reference_ask
                    or source[1].invalidation_price != witness.five_second.initial_stop
                    or journal.assignment_for_intent(source_entry_id) != financial.assignment_id
                    or type(record.payload.get('strategy_revision')) is not int
                    or record.payload['strategy_revision'] not in (34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52)):
                raise RuntimeError('AH confirmation requires its exact original typed entry source')
            confirmation = project_confirmed_ah_failure(
                witness, intent, financial, session_date=session_date,
                source_entry_intent_id=source_entry_id, run_id=batch.run_id,
                batch_id=batch.batch_id, parent_record_id=record.record_id,
                strategy_number=record.payload['strategy_revision'],
            )
            unit = V4ConfirmedAhFailureBatch(batch, confirmation)
            sources[intent.intent_id] = (batch, intent)
        if liquidity_source is not None:
            from src.trading_runtime.arte_liquidity_fade_failure_v4 import project_liquidity_fade_failure
            intent, witness, financial, source_entry_id, session_date, observation_source = liquidity_source
            payload = {key: value for key, value in record.payload.items()
                       if key not in {'strategy_id', 'strategy_revision', 'correlation_id', 'causation_id'}}
            source = sources.get(source_entry_id)
            if (record.entity_id != intent.intent_id or record.account_id != financial.account_id
                    or canonical_json(payload) != canonical_json(intent.payload())
                    or source is None or source[0].run_id != record.run_id
                    or not source[0].last_sequence < observation_source['source_manager_checkpoint_sequence'] < record.sequence
                    or len(source[0].intents) != 1 or len(source[0].events) != 1
                    or source[0].events[0]['account_id'] != record.account_id
                    or source[0].events[0]['entity_id'] != source_entry_id
                    or source[0].intents[0]['intent_id'] != source_entry_id
                    or source[1].intent_id != source_entry_id or source[1].ticker != intent.ticker
                    or source[1].action != 'enter_long' or source[1].reason != 'strategy_one_entry'
                    or source[1].reference_price != witness.reference_ask
                    or source[1].invalidation_price != witness.initial_stop
                    or journal.assignment_for_intent(source_entry_id) != financial.assignment_id
                    or type(record.payload.get('strategy_revision')) is not int
                    or record.payload['strategy_revision'] not in (35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52)
                    or record.payload.get('strategy_id') != 'early-squeeze-strategy'):
                raise RuntimeError('Liquidity exit requires its exact original typed entry source')
            failure = project_liquidity_fade_failure(witness, intent, financial,
                session_date=session_date, source_entry_intent_id=source_entry_id, run_id=batch.run_id,
                batch_id=batch.batch_id, parent_record_id=record.record_id,
                strategy_number=record.payload['strategy_revision'], **observation_source)
            unit = V4LiquidityFadeFailureBatch(batch, failure)
            sources[intent.intent_id] = (batch, intent)
        base = unit.base if not isinstance(unit, TypedJournalBatch) else unit
        if (base.first_sequence != sequence or base.last_sequence != sequence
                or len(base.events) != 1 or base.batch_id != batch_id
                or base.prior_batch_id != previous):
            raise ValueError("V4 projector changed the exclusive batch identity")
        if isinstance(unit, TypedJournalBatch):
            if (journal.strategy_one_protection_for_record(record.record_id) is not None
                    or journal.numbered_session_exit_for_record(record.record_id) is not None):
                if ordinary:
                    units.append(_coalesce_unpublished(tuple(ordinary)))
                    ordinary.clear()
                units.append(unit)
            else:
                ordinary.append(unit)
        else:
            if ordinary:
                units.append(_coalesce_unpublished(tuple(ordinary)))
                ordinary.clear()
            units.append(unit)
        previous = batch_id
    if ordinary:
        units.append(_coalesce_unpublished(tuple(ordinary)))
    return tuple(units)


def project_pending_backtest_prefix(
    journal: BacktestMemoryJournal, *, attempt_id: str, run_month: date,
    prior_sequence: int, prior_batch_id: str = NIL_BATCH_ID,
    source_cursor: str = "start", expected_config: dict | None = None,
    fixed_market_parent_plan: object | None = None,
    fixed_market_execution_plan: object | None = None,
    expected_market_start: datetime | None = None,
    through_sequence: int | None = None,
) -> ProjectedBacktestPrefix:
    """Project every pending record or reject the entire prefix before writes.

    Each logical event receives one deterministic typed batch. A completed
    market-boundary event advances the source cursor only within its own batch.
    """
    attempt = str(UUID(attempt_id))
    previous = str(UUID(prior_batch_id))
    if (run_month.day != 1 or type(prior_sequence) is not int
            or prior_sequence < 0 or not source_cursor
            or (prior_sequence == 0 and previous != NIL_BATCH_ID)
            or (prior_sequence > 0 and previous == NIL_BATCH_ID)):
        raise ValueError("Backtest typed prefix identity is invalid")
    if through_sequence is not None:
        if (type(through_sequence) is not int or through_sequence <= prior_sequence
                or through_sequence > journal.latest_sequence(journal.run_id)):
            raise ValueError("Backtest typed projection limit is outside pending records")
    records = journal.unfenced_records(after_sequence=prior_sequence,
                                       through_sequence=through_sequence)
    if through_sequence is not None and len(records) != through_sequence - prior_sequence:
        raise ValueError("Backtest typed projection limit is not contiguous")
    batches: list[TypedJournalBatch] = []
    cursor = source_cursor
    for sequence, record in enumerate(records, start=prior_sequence + 1):
        if record.run_id != journal.run_id or record.sequence != sequence:
            raise ValueError("Backtest typed prefix is not one contiguous run")
        canonical_json(record.payload)
        batch_id = str(uuid5(NAMESPACE_URL,
            f"arte-backtest-v1:{record.run_id}:{attempt}:{record.sequence}:{record.record_id}"))
        if (record.category, record.entity_type) == ("checkpoint", "market_boundary"):
            cursor = record.entity_id
        batch = project_journal_record(
            record, run_month=run_month, attempt_id=attempt,
            batch_id=batch_id, prior_batch_id=previous, source_cursor=cursor,
            expected_config=expected_config, expected_mode="backtest",
            fixed_market_parent_plan=fixed_market_parent_plan,
            fixed_market_execution_plan=fixed_market_execution_plan,
            expected_market_start=expected_market_start,
        )
        if (batch.run_id != journal.run_id or batch.first_sequence != sequence
                or batch.last_sequence != sequence or len(batch.events) != 1):
            raise ValueError("Backtest typed projector changed event identity")
        batches.append(batch)
        previous = batch_id
    return ProjectedBacktestPrefix(tuple(batches),
                                   prior_sequence + len(batches), previous, cursor)


def project_pending_backtest_v3_prefix(
    journal: BacktestMemoryJournal, *, attempt_id: str, run_month: date,
    prior_sequence: int, prior_batch_id: str = NIL_BATCH_ID,
    source_cursor: str = "start", expected_config: dict | None = None,
    fixed_market_parent_plan: object | None = None,
    fixed_market_execution_plan: object | None = None,
    expected_market_start: datetime | None = None,
    expected_market_plan_token: str, expected_query_sha256: str,
    through_sequence: int | None = None,
) -> tuple[V3SqueezeBatch, ...]:
    """Project a contiguous V3 prefix; every batch names its closed child set."""
    import re
    from src.backend.backtest_squeeze_episode_v3 import project_squeeze_batch_v3

    if (re.fullmatch(r"[0-9a-f]{64}", expected_market_plan_token) is None
            or re.fullmatch(r"[0-9a-f]{64}", expected_query_sha256) is None
            or run_month.day != 1 or prior_sequence < 0):
        raise ValueError("V3 projection lacks pinned source authority")
    attempt = str(UUID(attempt_id))
    previous = str(UUID(prior_batch_id))
    if (prior_sequence == 0) != (previous == NIL_BATCH_ID):
        raise ValueError("V3 projection prior batch identity differs")
    if through_sequence is not None:
        if (through_sequence <= prior_sequence
                or through_sequence > journal.latest_sequence(journal.run_id)):
            raise ValueError("V3 projection limit is outside pending records")
    records = journal.unfenced_records(after_sequence=prior_sequence,
                                       through_sequence=through_sequence)
    result: list[V3SqueezeBatch] = []
    cursor = source_cursor
    for sequence, record in enumerate(records, start=prior_sequence + 1):
        if record.run_id != journal.run_id or record.sequence != sequence:
            raise ValueError("V3 projection is not one contiguous run")
        canonical_json(record.payload)
        batch_id = str(uuid5(NAMESPACE_URL,
            f"arte-backtest-v3:{record.run_id}:{attempt}:{record.sequence}:{record.record_id}"))
        if (record.category, record.entity_type) == ("checkpoint", "market_boundary"):
            cursor = record.entity_id
        if (record.category, record.entity_type) == (
                "market_discovery_signal", "signal_occurrence"):
            unit = project_squeeze_batch_v3(
                record, run_month=run_month, attempt_id=attempt,
                batch_id=batch_id, prior_batch_id=previous,
                source_cursor=cursor,
                expected_market_plan_token=expected_market_plan_token,
                expected_query_sha256=expected_query_sha256)
        elif (record.category, record.entity_type) == (
                "portfolio_management", "portfolio_reconciliation"):
            from src.backend.backtest_reconciliation_v3 import project_reconciliation_v3

            reconciliation = project_reconciliation_v3(
                record, attempt_id=attempt, batch_id=batch_id,
                account_key=record.entity_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (reconciliation.event,),
                portfolio_reconciliation_events=(reconciliation.parent,))
            unit = V3SqueezeBatch(
                base, (), (), reconciliation.differences)
        elif (record.category, record.entity_type) == (
                "portfolio_management", "portfolio_control"):
            from src.backend.backtest_portfolio_control_v3 import project_portfolio_control_v3

            strategy_id = record.payload.get("strategy_id")
            account_key = (record.entity_id[:-(len(strategy_id) + 1)]
                           if record.payload.get("event") ==
                           "strategy_allocation_control_changed"
                           and isinstance(strategy_id, str)
                           and record.entity_id.endswith(f":{strategy_id}")
                           else record.entity_id)
            control = project_portfolio_control_v3(
                record, attempt_id=attempt, batch_id=batch_id,
                account_key=account_key)
            selections = ()
            if record.payload.get("event") == "portfolio_policy_selected":
                from src.backend.backtest_policy_selection_v3 import project_policy_selection_v3

                selections = (project_policy_selection_v3(
                    record, account_key=account_key),)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (control.event,))
            unit = V3SqueezeBatch(base, (), portfolio_controls=(control.detail,),
                                  policy_selections=selections)
        elif (record.category == "trade_proposal" and record.entity_type in
              {"trade_proposal_confirmed", "trade_proposal_result"}):
            from src.backend.backtest_trade_proposal_v3 import project_trade_proposal_v3

            proposal = project_trade_proposal_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (proposal.event,))
            unit = V3SqueezeBatch(
                base, (), trade_proposal_rows=proposal.rows)
        elif (record.category, record.entity_type) == (
                "broker_policy", "short_order_skipped"):
            from src.backend.backtest_broker_shortability_v3 import project_short_order_skip_v3

            projected = project_short_order_skip_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (projected.event,))
            unit = V3SqueezeBatch(base, (), short_order_skips=(projected.detail,))
        elif record.category == "broker_policy" and record.entity_type in {
                "order_reply_suppression", "order_warning_decision"}:
            from src.backend.backtest_broker_policy_v3 import project_broker_reply_policy_v3

            projected = project_broker_reply_policy_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (projected.event,))
            unit = V3SqueezeBatch(
                base, (), broker_reply_policy_events=(projected.detail,),
                broker_reply_policy_messages=projected.messages)
        elif (record.category, record.entity_type) == (
                "order_management", "entry_reprice_deferred"):
            from src.backend.backtest_entry_reprice_deferred_v3 import (
                project_entry_reprice_deferred_v3,
            )

            projected = project_entry_reprice_deferred_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (projected.event,))
            unit = V3SqueezeBatch(
                base, (), entry_reprice_deferred=(projected.detail,))
        elif (record.category, record.entity_type) == (
                "portfolio_management", "entry_reprice_capacity"):
            from src.backend.backtest_entry_reprice_capacity_v3 import (
                project_entry_reprice_capacity_v3,
            )

            projected = project_entry_reprice_capacity_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (projected.event,))
            unit = V3SqueezeBatch(
                base, (), entry_reprice_capacities=(projected.detail,),
                entry_reprice_capacity_reasons=projected.reasons)
        elif (record.category, record.entity_type) == (
                "portfolio_management", "entry_reprice_rejected"):
            from src.backend.backtest_entry_reprice_rejected_v3 import (
                project_entry_reprice_rejected_v3,
            )

            projected = project_entry_reprice_rejected_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (projected.event,))
            unit = V3SqueezeBatch(
                base, (), entry_reprice_rejections=(projected.detail,))
        elif (record.category, record.entity_type) == (
                "order_management", "protected_exit_already_satisfied"):
            from src.backend.backtest_protected_exit_satisfied_v3 import (
                project_protected_exit_satisfied_v3,
            )

            projected = project_protected_exit_satisfied_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (projected.event,))
            unit = V3SqueezeBatch(
                base, (), protected_exit_satisfied=(projected.detail,))
        elif (record.category, record.entity_type) == (
                "order_management", "protected_exit_snapshot_reconciled"):
            from src.backend.backtest_protected_exit_snapshot_v3 import (
                project_protected_exit_snapshot_v3,
            )

            projected = project_protected_exit_snapshot_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (projected.event,))
            unit = V3SqueezeBatch(
                base, (), protected_exit_snapshots=(projected.detail,))
        elif (record.category, record.entity_type) == (
                "portfolio_management", "portfolio_allocation"):
            from src.backend.backtest_portfolio_allocation_v3 import (
                project_portfolio_allocation_v3,
            )

            projected = project_portfolio_allocation_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (projected.event,))
            unit = V3SqueezeBatch(
                base, (), portfolio_allocation_fills=(projected.detail,))
        elif (record.category, record.entity_type) == (
                "protection", "protection_change"):
            from src.backend.backtest_protection_change_v3 import project_protection_change_v3

            projected = project_protection_change_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (projected.event,))
            unit = V3SqueezeBatch(
                base, (), protection_changes=(projected.detail,),
                protection_entry_orders=projected.entry_orders)
        else:
            reservation_reasons = ()
            if (record.category, record.entity_type) == (
                    "portfolio_management", "portfolio_reservation"):
                from src.backend.backtest_reservation_reason_v3 import (
                    project_reservation_reasons_v3,
                )
                reservation_reasons = project_reservation_reasons_v3(
                    record, batch_id=batch_id)
            base = project_journal_record(
                record, run_month=run_month, attempt_id=attempt,
                batch_id=batch_id, prior_batch_id=previous,
                source_cursor=cursor, expected_config=expected_config,
                expected_mode="backtest",
                fixed_market_parent_plan=fixed_market_parent_plan,
                fixed_market_execution_plan=fixed_market_execution_plan,
                expected_market_start=expected_market_start,
                allow_v3_reservation_reasons=True)
            unit = V3SqueezeBatch(base, (), reservation_reasons)
        if (unit.base.run_id != journal.run_id
                or unit.base.first_sequence != sequence
                or unit.base.last_sequence != sequence
                or len(unit.base.events) != 1):
            raise ValueError("V3 projector changed event identity")
        result.append(unit)
        previous = batch_id
    return tuple(result)
