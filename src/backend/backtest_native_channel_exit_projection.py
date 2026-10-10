"""Normalized pending exit projection; checkpoint admission remains separate."""
from dataclasses import asdict, dataclass
from hashlib import sha256
from math import isfinite

from .backtest_native_channel_exit_request import NativeChannelExitRequest
from .backtest_native_channel_exit_lineage import project_native_channel_exit_lineage
from .backtest_native_channel_exit_policy import NativeChannelExitPolicy
from src.market_engine.native_causal_channel_contract import NativeChannelSourcePlan
from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal, StrategyOneFinancialView
from src.trading_runtime.arte_liquidity_fade_failure_v4 import validate_liquidity_checkpoint_reference
from src.trading_runtime.arte_native_channel_exit_schema import DECISION
from src.trading_runtime.arte_journal_writer import typed_contract_row
from src.trading_runtime.journal_contract import canonical_json


@dataclass(frozen=True, slots=True)
class NativeChannelExitCheckpointReference:
    source_manager_snapshot_id: str
    source_manager_checkpoint_sequence: int
    source_manager_snapshot_hash: str
    source_broker_snapshot_id: str
    source_broker_snapshot_hash: str

    def __post_init__(self):
        # Shared pointer syntax, without relabeling a different exit witness.
        validate_liquidity_checkpoint_reference(asdict(self))


def project_native_channel_exit(request, group, executions, *, original_entry,
        source_entry_intent_id, current_financial, checkpoint, **lineage_context):
    """Bind original risk, current ownership and exact fills before serialization.

    These rows grant no order authority. The journal owner must independently
    verify source coverage/content, original entry and both checkpoint roots,
    then seal all three families together before admitting an exit.
    """
    if type(checkpoint) is not NativeChannelExitCheckpointReference:
        raise ValueError('Exact channel checkpoint references required')
    checkpoint.__post_init__()
    values, sources, fills = _project_native_channel_exit_facts(request, group, executions,
        original_entry=original_entry, source_entry_intent_id=source_entry_intent_id,
        current_financial=current_financial, **lineage_context)
    return typed_contract_row(DECISION, {**values, **asdict(checkpoint)}), sources, fills


def _project_native_channel_exit_facts(request, group, executions, *, original_entry,
        source_entry_intent_id, current_financial, **lineage_context):
    """Shared pre-checkpoint facts; never insert fabricated root references."""
    if (type(request) is not NativeChannelExitRequest
            or type(request.policy) is not NativeChannelExitPolicy
            or type(request.source_plan) is not NativeChannelSourcePlan
            or type(original_entry) is not StrategyOneEntryProposal
            or type(current_financial) is not StrategyOneFinancialView
            or current_financial != request.financial
            or current_financial.pending_exit
            or type(current_financial.position_quantity) not in (int, float)
            or not isfinite(current_financial.position_quantity)
            or current_financial.position_quantity <= 0):
        raise ValueError('Exact current held owner and original entry required')
    owner = (request.acquisition.account_id, request.acquisition.assignment_id, request.acquisition.ticker)
    if (owner != (original_entry.account_id, original_entry.assignment_id, original_entry.ticker)
            or source_entry_intent_id != request.acquisition.source_entry_intent_id
            or type(original_entry.strategy_number) is not int
            or not 0 < original_entry.strategy_number < 2**32
            or type(original_entry.boundary_ms) is not int
            or not 0 < original_entry.boundary_ms <= 57_600_000
            or original_entry.boundary_ms % 100
            or (original_entry.boundary_ms + 14_400_000) * 1000 > request.acquisition.acquisition_day_us):
        raise ValueError('Original entry is foreign or later than its actual acquisition')
    ask, stop = original_entry.reference_ask, original_entry.initial_stop
    if (any(type(v) not in (int, float) or not isfinite(v) for v in (ask, stop))
            or not 0 < stop < ask):
        raise ValueError('Exact finite original risk anchors required')
    decision = request.decision
    if request.policy.quote.threshold_mode == 'below_original_risk_threshold' and (
            decision.get('original_ask') != ask or decision.get('initial_stop') != stop):
        raise ValueError('Quote confirmation borrowed different original risk anchors')
    sources, fills = project_native_channel_exit_lineage(request, group, executions, **lineage_context)
    clock, plan = request.acquisition, request.source_plan
    plan.__post_init__()
    unit = plan.request.unit(clock.ticker)
    if (decision['build_id'] != plan.request.market.build_id
            or decision['attempt_id'] != unit.attempt_id
            or decision['feature_attempt_id'] != plan.feature_attempt_id
            or decision['policy_digest'] != plan.request.policy.digest
            or clock.session_date.isoformat() != plan.request.session_date
            or decision['decision_day_ms'] > plan.request.through_day_boundary_ms
            or request.policy.sequence.inputs != plan.request.policy):
        raise ValueError('Decision header differs from the declared feature source plan')
    age = decision['decision_day_ms'] * 1000 - decision['quote_day_us']
    if (type(age) is not int or not 0 <= age <= request.policy.quote.quote_max_age_us
            or decision['sequence_end_day_ms'] > decision['decision_day_ms']
            or decision['sequence_start_day_ms'] * 1000 <= clock.acquisition_day_us):
        raise ValueError('Pending decision source or quote clock differs from causal ownership')
    values = dict(record_id=lineage_context['decision_record_id'],
        parent_record_id=lineage_context['parent_record_id'], run_id=lineage_context['run_id'],
        event_month=clock.session_date.replace(day=1).isoformat(), batch_id=lineage_context['batch_id'],
        strategy_number=original_entry.strategy_number, session_date=clock.session_date.isoformat(),
        account_id=clock.account_id, assignment_id=clock.assignment_id, ticker=clock.ticker,
        source_entry_intent_id=clock.source_entry_intent_id, source_group_id=clock.source_group_id,
        first_execution_id=clock.first_execution_id, first_execution_time=clock.first_execution_time,
        acquisition_day_us=clock.acquisition_day_us, decision_day_ms=decision['decision_day_ms'],
        source_build_id=decision['build_id'], source_bars_attempt_id=decision['attempt_id'],
        source_feature_attempt_id=plan.feature_attempt_id, source_market_plan_token=plan.request.market.token,
        source_feature_plan_token=plan.token, source_feature_hash=plan.feature_hash,
        source_coverage_hash=plan.coverage_hash, source_producer_hash=plan.producer_source_hash,
        input_policy_digest=plan.request.policy.digest,
        exit_policy_digest=sha256(canonical_json(request.policy.payload()).encode()).hexdigest(),
        sequence_start_day_ms=decision['sequence_start_day_ms'], sequence_end_day_ms=decision['sequence_end_day_ms'],
        reference_completion_day_ms=decision.get('reference_completion_day_ms'),
        reference_close_int=decision.get('reference_close_int'), quote_day_us=decision['quote_day_us'],
        quote_age_us=age, bid_int=decision['bid_int'], ask_int=decision['ask_int'],
        original_ask=ask, initial_stop=stop, position_quantity=current_financial.position_quantity,
        source_row_count=len(sources), execution_row_count=len(fills))
    return values, sources, fills
