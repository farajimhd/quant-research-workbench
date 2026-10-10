"""Complete normalized channel exit transport; writer/OMS admission is separate.

This module creates no tables, writes, reservations or orders. It preserves
the shared urgent-exit execution policy and the actual held quantity.
"""
from dataclasses import dataclass, fields
from datetime import date, datetime, time, timezone
from decimal import Decimal
import json
from types import MappingProxyType
from uuid import NAMESPACE_URL, uuid5

from .backtest_market_data import SESSION_OPEN_OFFSET_MS, market_day_boundary
from .backtest_native_channel_exit_projection import project_native_channel_exit
from .backtest_native_channel_exit_request import NativeChannelExitRequest
from src.trading_runtime.arte_native_channel_exit_schema import TABLES
from src.trading_runtime.arte_journal_writer import TypedJournalBatch, _sealed_families, verify_typed_contract_row
from src.trading_runtime.native_channel_management_policy import NativeChannelManagementPolicy
from src.trading_runtime.journal_contract import canonical_json


def native_channel_exit_intent(request, *, strategy_number, management_policy):
    """Build a deterministic desired liquidation, without granting authority.

    The registered immutable release must select this exact declared policy.
    Native checkpoint/source verification and shared Portfolio/OMS approval
    remain mandatory before the intent may become an executable order.
    """
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy, session_policy_payload
    from src.trading_runtime.strategy_liquidity_fade_exit import validate_liquidity_fade_financial
    from src.trading_runtime.execution_policies import (
        ExecutionEnvelope, ExecutionPolicy, ExecutionPolicyName, PartialFillPolicy,
    )
    from src.trading_runtime.signals import StrategyIntent
    if (type(request) is not NativeChannelExitRequest
            or type(management_policy) is not NativeChannelManagementPolicy
            or type(strategy_number) is not int or not 0 < strategy_number < 2**32
            or request.policy != management_policy.exit_policy
            or request.decision.get('exit_eligible') is not True):
        raise ValueError('Channel intent lacks its exact registered declaration and accepted request')
    contract = numbered_fixed_strategy(strategy_number)
    if contract.native_channel_management_policy != management_policy:
        raise ValueError('Channel intent differs from the immutable registered management policy')
    management_policy.__post_init__()
    validate_liquidity_fade_financial(request.financial)
    request.acquisition.__post_init__()
    financial, clock = request.financial, request.acquisition
    if (clock.account_id, clock.assignment_id, clock.ticker) != (
            financial.account_id, financial.assignment_id, financial.ticker):
        raise ValueError('Channel intent differs from actual acquisition ownership')
    day_ms, bid = request.decision['decision_day_ms'], request.decision['bid_int']
    if (type(day_ms) is not int or type(bid) is not int or not 0 < bid < 2**64):
        raise ValueError('Channel intent lacks its exact source clock and native bid')
    at = market_day_boundary(clock.session_date, day_ms - SESSION_OPEN_OFFSET_MS)
    sessions = json.loads(contract.policy_json).get('session_policy')
    if (sessions != session_policy_payload() or not any(
            time.fromisoformat(window['start']) <= at.time() < time.fromisoformat(window['end'])
            for window in sessions['windows'])):
        raise ValueError('Channel exit is outside the declared extended-session decision windows')
    identity = canonical_json(dict(rule=management_policy.payload(),strategy_number=strategy_number,
        session_date=clock.session_date.isoformat(),account_id=financial.account_id,
        assignment_id=financial.assignment_id,ticker=financial.ticker,
        source_entry_intent_id=clock.source_entry_intent_id,source_group_id=clock.source_group_id,
        decision=dict(request.decision),held_quantity=financial.position_quantity,
        source_plan_token=request.source_plan.token))
    # Integer prices use the canonical SIP contract's 1/10000 dollar unit.
    return StrategyIntent(intent_id=str(uuid5(NAMESPACE_URL,identity)),ticker=financial.ticker,
        event_time=at.astimezone(timezone.utc),action='exit',quantity=float(financial.position_quantity),
        reference_price=float(Decimal(bid)/Decimal(10_000)),urgency='urgent',outside_rth=True,
        reason=f'strategy_{strategy_number}_native_completed_channel_exit',metadata={},
        execution_policy=ExecutionPolicy(policy_id='strategy-adaptive_urgent',
            name=ExecutionPolicyName.ADAPTIVE_URGENT,
            envelope=ExecutionEnvelope(persist_until_cancelled=True),
            partial_fill_policy=PartialFillPolicy.COMPLETE_REMAINDER,quote_source='qmd'))


@dataclass(frozen=True, slots=True)
class NativeChannelExitUnit:
    base: TypedJournalBatch
    decision: object
    sources: tuple
    executions: tuple

    def __post_init__(self):
        if (type(self.base) is not TypedJournalBatch or self.base.status != 'running'
                or self.base.first_sequence != self.base.last_sequence
                or len(self.base.events) != 1 or len(self.base.intents) != 1
                or any(getattr(self.base,field.name) for field in fields(TypedJournalBatch)
                       if field.name not in {'run_id','run_month','attempt_id','batch_id','prior_batch_id',
                           'first_sequence','last_sequence','source_cursor','status','events','intents'})):
            raise ValueError('Channel exit unit requires one complete scalar intent batch')
        event, intent = self.base.events[0], self.base.intents[0]
        families = ((self.decision,), self.sources, self.executions)
        for contract, rows in zip(TABLES, families, strict=True):
            if type(rows) is not tuple:
                raise ValueError('Channel exit unit requires complete immutable child inventories')
            for row in rows:
                verify_typed_contract_row(contract,row)
                if (row['run_id'] != self.base.run_id or row['batch_id'] != self.base.batch_id
                        or row['parent_record_id'] != intent['record_id']
                        or row['event_month'] != self.base.run_month.isoformat()):
                    raise ValueError('Channel evidence differs from the exact intent batch envelope')
            if len({row['record_id'] for row in rows}) != len(rows):
                raise ValueError('Channel exit unit repeats a source or execution row identity')
        header = self.decision
        if (event['record_id'] != intent['record_id'] or event['entity_id'] != intent['intent_id']
                or event['category'] != 'strategy' or event['entity_type'] != 'strategy_intent'
                or header['source_row_count'] != len(self.sources)
                or header['execution_row_count'] != len(self.executions)
                or header['source_manager_checkpoint_sequence'] >= self.base.first_sequence
                or any(row['decision_record_id'] != header['record_id'] for row in (*self.sources,*self.executions))
                or intent['action'] != 'exit' or intent['ticker'] != header['ticker']
                or intent['account_id'] != header['account_id']
                or Decimal(str(intent['quantity'])) != Decimal(str(header['position_quantity']))
                or Decimal(str(intent['reference_price'])) != Decimal(str(float(Decimal(header['bid_int'])/Decimal(10_000))))
                or intent['reason'] != f"strategy_{header['strategy_number']}_native_completed_channel_exit"):
            raise ValueError('Channel exit unit loses its exact parent or complete source/fill graph')
        at = datetime.fromisoformat(str(event['event_time']).replace('Z','+00:00'))
        expected = market_day_boundary(date.fromisoformat(header['session_date']),
            header['decision_day_ms']-SESSION_OPEN_OFFSET_MS)
        if at.tzinfo is None or at != expected:
            raise ValueError('Channel intent event differs from the causal decision clock')
        object.__setattr__(self,'decision',MappingProxyType(dict(header)))
        object.__setattr__(self,'sources',tuple(MappingProxyType(dict(row)) for row in self.sources))
        object.__setattr__(self,'executions',tuple(MappingProxyType(dict(row)) for row in self.executions))

    def sealed_families(self):
        """Complete V4 commit inventory; not permission to INSERT these tables."""
        self.__post_init__()
        return tuple(_sealed_families(self.base)) + tuple((contract.name,
            tuple(verify_typed_contract_row(contract,row) for row in rows))
            for contract,rows in zip(TABLES,((self.decision,),self.sources,self.executions),strict=True))


def project_native_channel_exit_unit(request, group, executions, *, management_policy,
        original_entry, checkpoint, recorded_at, attempt_id, prior_batch_id, sequence,
        source_cursor, **lineage_context):
    """Link the exact factory intent to every normalized causal source/fill row."""
    from src.trading_runtime.arte_intent_projection import strategy_intent_batch
    if (type(management_policy) is not NativeChannelManagementPolicy
            or lineage_context.get('max_rows_per_family') != management_policy.max_rows_per_family):
        raise ValueError('Channel exit publication differs from its declared complete-family bound')
    intent = native_channel_exit_intent(request,strategy_number=original_entry.strategy_number,
        management_policy=management_policy)
    base = strategy_intent_batch(intent,run_id=lineage_context['run_id'],
        run_month=request.acquisition.session_date.replace(day=1),account_id=request.financial.account_id,
        attempt_id=attempt_id,batch_id=lineage_context['batch_id'],prior_batch_id=prior_batch_id,
        sequence=sequence,source_cursor=source_cursor,run_status='running',recorded_at=recorded_at,
        record_id=lineage_context['parent_record_id'])
    decision, sources, fills = project_native_channel_exit(request,group,executions,
        original_entry=original_entry,source_entry_intent_id=request.acquisition.source_entry_intent_id,
        current_financial=request.financial,checkpoint=checkpoint,**lineage_context)
    return NativeChannelExitUnit(base,decision,sources,fills)


def reprove_native_channel_exit_unit(unit, *, management_policy, recorded_at,
        attempt_id, prior_batch_id, sequence, source_cursor, **recovery_context):
    """Recompute the complete unit from independently supplied causal evidence.

    The caller owns the certified source, actual fills, held financial state,
    checkpoint roots and journal envelope. No value in the proposed base batch
    supplies those authorities. This grants neither INSERT nor OMS permission.
    """
    from .backtest_native_channel_exit_recovery import reprove_native_channel_exit_rows
    if type(unit) is not NativeChannelExitUnit:
        raise ValueError('Exact complete channel exit unit required')
    unit.__post_init__()
    request = reprove_native_channel_exit_rows(unit.decision,unit.sources,unit.executions,
        **recovery_context)
    projection_context = {key:value for key,value in recovery_context.items() if key not in
        {'prepared','policy','quotes','session_date','stored_utc','group','executions',
         'source_entry_intent_id','current_financial'}}
    expected = project_native_channel_exit_unit(request,recovery_context['group'],
        recovery_context['executions'],management_policy=management_policy,
        recorded_at=recorded_at,attempt_id=attempt_id,prior_batch_id=prior_batch_id,
        sequence=sequence,source_cursor=source_cursor,**projection_context)
    if unit.base != expected.base or unit.sealed_families() != expected.sealed_families():
        raise ValueError('Complete channel exit unit differs from independent recomputation')
    return expected


def prepare_confirmed_native_channel_exit_unit(client, manager_keeper, broker_keeper,
        receipt, *, run_id, management_policy, contexts, pending_record_id,
        first_price_source, recorded_at, attempt_id, batch_id, parent_record_id,
        decision_record_id):
    """Prepare the next scalar journal unit from actual checkpoint ownership.

    This cold worker operation verifies the complete pending inventory, both
    selected roots, original committed fills and the held OMS position. The
    exit quantity, original entry and checkpoint references are never supplied
    by the publication caller. Writer fencing and OMS admission remain required.
    """
    from collections.abc import Mapping
    from .backtest_native_channel_financial_checkpoint import confirm_native_channel_held_checkpoint
    from .backtest_typed_publisher import TypedBacktestReceipt
    if (type(receipt) is not TypedBacktestReceipt or not isinstance(contexts,Mapping)
            or pending_record_id not in contexts):
        raise ValueError('Channel unit requires its exact pending context and committed receipt')
    if any(not isinstance(context,Mapping) for context in contexts.values()):
        raise ValueError('Channel unit requires complete independent pending evidence')
    contexts={key:dict(context) for key,context in contexts.items()}
    held = confirm_native_channel_held_checkpoint(client,manager_keeper,broker_keeper,receipt,
        run_id=run_id,policy=management_policy,contexts=contexts,first_price_source=first_price_source)
    # The manager verifier consumes this same deterministic inventory order.
    keys = sorted(contexts)
    if len(held) != len(keys):
        raise ValueError('Confirmed channel inventory differs from exact pending contexts')
    confirmed = held[keys.index(pending_record_id)]
    context = dict(contexts[pending_record_id])
    if (context['run_id'] != run_id or context['batch_id'] != receipt.last_batch_id
            or context['current_financial'] != confirmed.request.financial
            or confirmed.checkpoint.source_manager_checkpoint_sequence != receipt.last_sequence):
        raise ValueError('Channel publication crosses its confirmed pending frontier or held owner')
    context.update(checkpoint=confirmed.checkpoint,batch_id=batch_id,
        parent_record_id=parent_record_id,decision_record_id=decision_record_id)
    envelope=dict(management_policy=management_policy,recorded_at=recorded_at,
        attempt_id=attempt_id,prior_batch_id=receipt.last_batch_id,
        sequence=receipt.last_sequence+1,source_cursor=receipt.source_cursor)
    projection_context={key:value for key,value in context.items() if key not in
        {'prepared','policy','quotes','session_date','group','executions',
         'source_entry_intent_id','current_financial'}}
    unit=project_native_channel_exit_unit(confirmed.request,context['group'],context['executions'],
        **envelope,**projection_context)
    return reprove_native_channel_exit_unit(unit,**envelope,**context)
