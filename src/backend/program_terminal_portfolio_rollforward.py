"""Blocked Portfolio policy rollforward from an attested terminal closing tail.

The caller must independently verify the full V4 tail seal, closing executions
and their causal OMS reservation joins. No actor admission, broker operation,
market clock, policy selection or checkpoint publication occurs here.
"""
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass,fields,replace
from datetime import datetime,timezone
from decimal import Decimal
from math import isfinite

from .backtest_journal_memory import BacktestMemoryJournal
from .program_financial_recovery import build_program_portfolio_engine
from .program_terminal_financial_rollforward import TerminalClosingTailScope,RecordedClosingExecution
from src.trading_runtime.arte_portfolio_recovery import PortfolioRecovery
from src.trading_runtime.arte_oms_projection import _verified_rows
from src.trading_runtime.portfolio import PortfolioReservation


@dataclass(frozen=True)
class TerminalPortfolioPolicyResult:
    recovery: PortfolioRecovery
    allocation_events_applied: tuple
    reservation_events_applied: int


def qualify_terminal_portfolio_final_sync(initial, *, broker_result, simulation_config):
    """Prove final-only native synchronization cannot lose a Portfolio peak.

    The broker's single-account equity extrema include every mark/fill. They
    conservatively bound all Portfolio synchronization observations. A possible
    new peak requires a separate exact cadence reconstruction and fails here.
    """
    from .program_terminal_financial_rollforward import TerminalClosingAccountingResult
    from src.trading_runtime.simulated_broker import SimulationConfig
    if (type(initial) is not PortfolioRecovery or len(initial.states)!=1
            or type(broker_result) is not TerminalClosingAccountingResult
            or type(simulation_config) is not SimulationConfig
            or initial.run_id!=broker_result.scope.run_id):
        raise ValueError('Terminal Portfolio peak proof requires its single-account accounting result')
    account,state=next(iter(initial.states.items()))
    image=broker_result.state
    performance=image.get('performance_extrema',{})
    if (image.get('account_ids')!=[account] or set(image.get('cash',{}))!={account}
            or performance.get('complete') is not True
            or state.realized_pnl_baseline is None
            or not isfinite(float(state.realized_pnl_baseline))
            or not isfinite(float(state.peak_net_liquidation))
            or not isfinite(float(performance.get('equity_peak',float('nan'))))
            or not isfinite(float(simulation_config.initial_cash))
            or simulation_config.initial_cash<=0
            or float(simulation_config.initial_cash)+float(performance['equity_peak'])
                > state.peak_net_liquidation+1e-9):
        raise ValueError('Terminal Portfolio peak/baseline needs exact intermediate synchronization')


def _clock(value):
    if isinstance(value,str):
        value=datetime.fromisoformat(value.replace('Z','+00:00'))
        # Normalized ClickHouse columns declare UTC even when JSON omits it.
        if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
    if type(value) is not datetime or value.tzinfo is None:raise ValueError('Terminal Portfolio needs an exact UTC clock')
    return value.astimezone(timezone.utc)


def _reservation(row):
    values={field.name:row[field.name] for field in fields(PortfolioReservation)}
    values['created_at']=_clock(values['created_at'])
    integer={'admission_epoch','cash_tranche_count','cash_tranche_next'}
    for name in integer:values[name]=int(values[name])
    for name in ('quantity','remaining_quantity','reference_price','reserved_notional','reserved_planned_risk','filled_quantity','reserved_entry_fees','cash_tranche_size','cash_tranche_budget'):
        values[name]=float(values[name])
        if not isfinite(values[name]) or values[name]<0:raise ValueError('Terminal Portfolio reservation number is invalid')
    result=PortfolioReservation(**values)
    if result.remaining_quantity>result.quantity or result.filled_quantity>result.quantity:raise ValueError('Terminal Portfolio reservation quantity exceeds approval')
    return result


def rollforward_terminal_portfolio_policy(initial,*,scope,events,reservation_rows,allocation_rows,decision_rows,
        executions,execution_reservations,strategy_id,strategy_revision,groups=()):
    """Apply recorded reservation controls and native full-closing lot deltas.

    The result deliberately retains blocked broker synchronization state. The
    independently qualified final broker must supply balances/positions before
    capturing or publishing a new account snapshot.
    """
    if (type(initial) is not PortfolioRecovery or type(scope) is not TerminalClosingTailScope
            or initial.run_id!=scope.run_id or not initial.states
            or set(initial.revisions)!=set(initial.states)
            or any(value!=scope.first_sequence-1 for value in initial.revisions.values())
            or type(execution_reservations) is not dict or not 1<=len(executions)<=256):
        raise ValueError('Terminal Portfolio policy lacks its exact old image and bounded scope')
    events=tuple(events);reservation_rows=tuple(reservation_rows);allocation_rows=tuple(allocation_rows);decision_rows=tuple(decision_rows);executions=tuple(executions)
    if len(events)!=scope.last_sequence-scope.first_sequence+1 or len(events)>4096 or len(reservation_rows)>4096 or len(allocation_rows)>256:
        raise ValueError('Terminal Portfolio tail coverage exceeds its explicit bound')
    for name,rows in (('trading_event_v1',events),('trading_portfolio_reservation_event_v1',reservation_rows),('trading_portfolio_allocation_fill_v4',allocation_rows),('trading_portfolio_decision_v1',decision_rows)):
        _verified_rows(name,list(rows))
    ordered=sorted(events,key=lambda row:row['sequence'])
    if ([row['sequence'] for row in ordered]!=list(range(scope.first_sequence,scope.last_sequence+1))
            or len({row['record_id'] for row in ordered})!=len(ordered)
            or any(row['run_id']!=scope.run_id or not scope.start_at<=_clock(row['event_time'])<=scope.stop_at for row in ordered)):
        raise ValueError('Terminal Portfolio tail event identity/chronology differs')
    by_id={row['record_id']:row for row in ordered}
    by_sequence={row['sequence']:row for row in ordered}
    def linked(row,kind):
        event=by_id.get(row['record_id'])
        if (event is None or row['run_id']!=scope.run_id or row['batch_id']!=event['batch_id']
                or row['account_id']!=event['account_id'] or row['account_id'] not in initial.states
                or (event['category'],event['entity_type'])!=('portfolio_management',kind)):
            raise ValueError('Terminal Portfolio detail lacks its exact journal envelope')
        return event
    decisions={row['reservation_id']:row for row in decision_rows}
    if len(decisions)!=len(decision_rows):raise ValueError('Terminal Portfolio repeats a reservation admission')
    for row in decision_rows:linked(row,'portfolio_decision')
    by_execution={item.execution.execution_id:item for item in executions if type(item) is RecordedClosingExecution}
    if len(by_execution)!=len(executions) or set(execution_reservations)!=set(by_execution):raise ValueError('Terminal Portfolio execution/reservation coverage differs')
    joined={}
    for identity,reservation_id in execution_reservations.items():
        item=by_execution[identity];execution=item.execution
        if reservation_id in joined or execution.side.upper() not in {'S','SELL'} or not scope.first_sequence<=item.sequence<item.commission_sequence<=scope.last_sequence:
            raise ValueError('Terminal Portfolio closing execution is duplicate or unordered')
        for sequence,kind in ((item.sequence,'fill'),(item.commission_sequence,'commission')):
            event=by_sequence.get(sequence)
            if (event is None or event['category']!='execution' or event['entity_type']!=kind
                    or event['entity_id']!=identity or event['account_id']!=execution.account):
                raise ValueError('Terminal Portfolio execution lacks its exact journal envelope')
        joined[reservation_id]=item
    current=scope.start_at
    journal=BacktestMemoryJournal(run_id=scope.run_id,initial_sequence=scope.first_sequence-1)
    engine=build_program_portfolio_engine(initial,journal=journal,strategy_id=strategy_id,strategy_revision=strategy_revision,groups=groups,event_clock=lambda:current)
    recorded=[(linked(row,'portfolio_reservation')['sequence'],'reservation',row) for row in reservation_rows]
    recorded.extend((linked(row,'portfolio_allocation')['sequence'],'allocation',row) for row in allocation_rows)
    if len({seq for seq,_,_ in recorded})!=len(recorded):raise ValueError('Terminal Portfolio repeats a typed event')
    applied=[];seen_fills=set()
    immutable=('reservation_id','decision_id','intent_id','account_key','account_id','strategy_id','assignment_id','ticker','action','quantity','reference_price','created_at','admission_epoch','admission_owner','cash_tranche_key','cash_tranche_size','cash_tranche_count','cash_tranche_next','cash_tranche_budget')
    try:
        for sequence,kind,row in sorted(recorded):
            current=_clock(by_id[row['record_id']]['event_time'])
            if kind=='reservation':
                value=_reservation(row);prior=engine.reservations.get(value.reservation_id)
                if value.strategy_id!=strategy_id:raise ValueError('Terminal Portfolio reservation has a foreign strategy')
                if row['event']=='reservation_created':
                    decision=decisions.get(value.reservation_id)
                    if (prior is not None or value.action!='exit' or value.status!='reserved' or value.created_at!=current
                            or decision is None or decision['decision_id']!=value.decision_id or decision['request_id']!=value.intent_id
                            or decision['status']!='approved' or decision['action']!='exit'
                            or Decimal(str(value.quantity))!=Decimal(decision['approved_quantity'])
                            or value.reserved_notional or value.reserved_planned_risk or value.reserved_entry_fees):
                        raise ValueError('Terminal Portfolio closing reservation lacks recorded admission')
                elif row['event'] in {'reservation_updated','reservation_released'}:
                    if prior is None or any(getattr(prior,key)!=getattr(value,key) for key in immutable) or value.filled_quantity<prior.filled_quantity:
                        raise ValueError('Terminal Portfolio reservation changes immutable admission or loses fills')
                else:raise ValueError('Terminal Portfolio tail changes unsupported controls')
                engine.reservations[value.reservation_id]=value
                engine._last_filled_by_reservation[value.reservation_id]=value.filled_quantity
            else:
                candidates=[reservation for reservation in engine.reservations.values() if reservation.reservation_id in joined
                    and reservation.account_id==row['account_id'] and reservation.assignment_id==row['assignment_id'] and reservation.ticker==row['ticker']]
                if len(candidates)!=1:raise ValueError('Terminal Portfolio allocation has no unique closing reservation')
                reservation=candidates[0];item=joined[reservation.reservation_id];execution=item.execution
                key=f'{reservation.account_id}:{strategy_id}:{reservation.assignment_id or reservation.ticker}:{reservation.ticker}'
                old=engine.allocations.get(key)
                if (row['action']!='exit' or row['strategy_id']!=strategy_id or by_id[row['record_id']]['entity_id']!=key
                        or old is None or old.quantity<=0 or Decimal(row['quantity'])!=0
                        or Decimal(row['incremental_quantity'])!=-Decimal(str(execution.size)) or old.quantity!=execution.size
                        or execution.account!=reservation.account_id or execution.symbol!=reservation.ticker
                        or not item.sequence<sequence or execution.execution_id in seen_fills):
                    raise ValueError('Terminal Portfolio allocation is not one recorded full closing fill')
                before=journal.latest_sequence(scope.run_id)
                engine._apply_fill(reservation,float(execution.size),action='exit')
                produced=journal.records(scope.run_id,after_sequence=before)
                if len(produced)!=1 or produced[0].entity_type!='portfolio_allocation' or engine.allocations.get(key) is not None:
                    raise ValueError('Terminal Portfolio owner emitted additional unqualified lifecycle work')
                if Decimal(str(produced[0].payload['incremental_quantity']))!=Decimal(row['incremental_quantity']) or produced[0].payload['quantity']!=0:
                    raise ValueError('Terminal Portfolio native allocation projection differs')
                seen_fills.add(execution.execution_id);applied.append(row['record_id'])
        if seen_fills!=set(by_execution) or engine.allocations or any(reservation.remaining_quantity or reservation.reserved_notional or reservation.reserved_planned_risk or reservation.reserved_entry_fees for reservation in engine.reservations.values() if reservation.reservation_id in joined):
            raise ValueError('Terminal Portfolio tail does not close every recorded allocation/reservation')
        recovery=PortfolioRecovery(scope.run_id,{account:scope.last_sequence for account in initial.states},deepcopy(engine.states),deepcopy(engine.reservations),deepcopy(engine.allocations),deepcopy(engine.differences),deepcopy(engine._last_filled_by_reservation))
        return TerminalPortfolioPolicyResult(recovery,tuple(applied),len(reservation_rows))
    finally:journal.close()

@dataclass(frozen=True)
class TerminalOmsObservationResult:
    image: object
    observations: object
    changed_broker_order_ids: tuple


def rollforward_terminal_oms_observations(initial_image,final_image,*,scope,closing_orders,
        executions,cancellations,broker_snapshot):
    """Project complete observations from old caches and attested terminal orders.

    ``final_image`` must come from normal complete typed lineage/protection
    reconstruction at the final prefix. Closing LiveOrders require independent
    recorded acknowledgement/fill/cancellation authority; requests alone cannot
    prove an order state. Unchanged historical fingerprints remain exact.
    """
    from src.trading_runtime.arte_oms_actor_restore import TypedOmsActorImage,verify_typed_oms_broker_open_orders
    from src.trading_runtime.arte_oms_observation_snapshot import project_shared_oms_observations
    from src.trading_runtime.arte_broker_match_snapshot import SharedBrokerMatchSnapshot,_codec_rows
    from src.trading_runtime.ibkr_schema import LiveOrder,OrderStatus
    from src.trading_runtime.order_management import _live_order_state_fingerprint
    if (type(initial_image) is not TypedOmsActorImage or type(final_image) is not TypedOmsActorImage
            or type(scope) is not TerminalClosingTailScope or type(broker_snapshot) is not SharedBrokerMatchSnapshot
            or (initial_image.run_id,initial_image.strategy_id,initial_image.strategy_revision)!=(final_image.run_id,final_image.strategy_id,final_image.strategy_revision)
            or final_image.run_id!=scope.run_id or broker_snapshot.snapshot['checkpoint_sequence']!=scope.last_sequence
            or broker_snapshot.snapshot['run_id']!=scope.run_id
            or any(group.broker_order_state_fingerprints for group in final_image.groups.values())):
        raise ValueError('Terminal OMS projection requires detached complete lineage images')
    old_bindings=initial_image.group_by_broker_id;new_bindings=final_image.group_by_broker_id
    if not set(old_bindings)<=set(new_bindings):raise ValueError('Terminal OMS lost historical broker ownership')
    changed=set(new_bindings)-set(old_bindings)
    for identity,group_id in old_bindings.items():
        if new_bindings[identity]!=group_id:raise ValueError('Terminal OMS broker ownership moved to another group')
        old=initial_image.groups[group_id];new=final_image.groups[group_id]
        old_index=old.broker_order_request_indexes.get(identity)
        new_index=new.broker_order_request_indexes.get(identity)
        if (old_index is None or new_index is None or not 0<=old_index<len(old.orders)
                or not 0<=new_index<len(new.orders) or old.orders[old_index]!=new.orders[new_index]
                or old.broker_order_roles.get(identity)!=new.broker_order_roles.get(identity)
                or old.broker_order_slices.get(identity)!=new.broker_order_slices.get(identity)):
            raise ValueError('Terminal OMS historical request/protection binding changed')
        if ((identity in old.terminal_broker_order_ids)!=(identity in new.terminal_broker_order_ids)
                or old.filled_by_broker_order.get(identity,0)!=new.filled_by_broker_order.get(identity,0)):
            changed.add(identity)
    orders=tuple(closing_orders);by_order={row.orderId:row for row in orders if type(row) is LiveOrder}
    by_fill={row.execution.order_id:row for row in executions if type(row) is RecordedClosingExecution}
    from .program_terminal_financial_rollforward import RecordedClosingCancellation
    by_cancel={row.broker_order_id:row for row in cancellations if type(row) is RecordedClosingCancellation}
    if (not 1<=len(orders)<=512 or len(by_order)!=len(orders) or set(by_order)!=changed
            or len(by_fill)!=len(executions) or len(by_cancel)!=len(cancellations)
            or set(by_fill)&set(by_cancel) or set(by_order)!=set(by_fill)|set(by_cancel)):
        raise ValueError('Terminal OMS affected-order evidence coverage differs')
    result=deepcopy(final_image)
    for identity,group_id in new_bindings.items():
        group=result.groups[group_id]
        if identity not in group.terminal_broker_order_ids:raise ValueError('Terminal OMS lineage retains an unconfirmed open binding')
        if identity not in changed:
            previous=initial_image.groups[group_id].broker_order_state_fingerprints.get(identity)
            if previous is None:raise ValueError('Terminal OMS lost its unchanged prior observation')
            group.broker_order_state_fingerprints[identity]=deepcopy(previous)
            continue
        order=by_order[identity];index=group.broker_order_request_indexes.get(identity)
        if index is None or not 0<=index<len(group.orders):raise ValueError('Terminal OMS order lacks its exact request binding')
        request=group.orders[index]
        if ((order.account,order.conid,order.ticker,order.cOID,order.side.upper(),order.orderType.upper(),order.tif.upper())
                !=(request.acctId,request.conid,request.ticker,request.cOID,request.side.upper(),request.orderType.upper(),request.tif.upper())
                or order.totalSize!=float(request.quantity) or order.price!=request.price or order.auxPrice!=request.auxPrice
                or order.filledQuantity!=group.filled_by_broker_order.get(identity,0)):
            raise ValueError('Terminal OMS observed order differs from its verified request/fill binding')
        if identity in by_fill:
            fact=by_fill[identity];execution=fact.execution
            if (order.order_status!=OrderStatus.FILLED or order.remainingQuantity!=0
                    or order.filledQuantity!=execution.size or order.avgPrice!=execution.price
                    or execution.account!=order.account or execution.symbol!=order.ticker or execution.order_ref!=order.cOID
                    or not scope.first_sequence<=fact.sequence<fact.commission_sequence<=scope.last_sequence):
                raise ValueError('Terminal OMS filled observation differs from recorded full execution')
        else:
            fact=by_cancel[identity];old=initial_image.groups[group_id].broker_order_state_fingerprints.get(identity)
            if (old is None or len(old)!=7 or order.order_status!=OrderStatus.CANCELLED
                    or order.account!=fact.account_id or not scope.start_at<=fact.at<=scope.stop_at
                    or not scope.first_sequence<=fact.sequence<=scope.last_sequence
                    or (order.filledQuantity,order.remainingQuantity,order.avgPrice,float(order.price or 0),float(order.auxPrice or 0))!=tuple(old[1:6])
                    or order.statusDescription!='Cancelled by client request'):
                raise ValueError('Terminal OMS cancellation lacks matching confirmed native evidence')
        group.broker_order_state_fingerprints[identity]=_live_order_state_fingerprint(order)
    codec=_codec_rows(broker_snapshot)
    verify_typed_oms_broker_open_orders(result,codec)
    from .backtest_market_data import market_day_boundary
    root=broker_snapshot.snapshot
    if market_day_boundary(root['session_date'],root['boundary_ms'])!=scope.stop_at:raise ValueError('Terminal OMS final broker clock differs')
    observations=project_shared_oms_observations(run_id=scope.run_id,session_date=datetime.fromisoformat(root['session_date']).date(),checkpoint_sequence=scope.last_sequence,boundary_ms=root['boundary_ms'],groups=result.groups)
    return TerminalOmsObservationResult(result,observations,tuple(sorted(changed)))
