"""Pure accounting rollforward for a sealed, flat terminal closing tail.

Inputs must already have independent journal/market/OMS authority. These bounded
projections create no strategy, matching runtime, checkpoint publication or
restart permission. An open final book requires a different recovery contract.
"""
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
from math import isfinite
import re

from src.trading_runtime.ibkr_schema import Execution, OrderRequest, OrderStatus, OPEN_ORDER_STATUSES
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.domain import TradingMode
from src.trading_runtime.arte_broker_match_snapshot import (
    SharedBrokerMatchSnapshot, verify_shared_broker_snapshot, project_shared_broker_snapshot,
)
from src.trading_runtime.backtest_broker_contract import BacktestBrokerContract
from .backtest_market_data import market_day_boundary

MARK_COLUMNS = frozenset(('ticker','resolution_ms','bucket_index','event_count',
    'first_event_us','last_event_us','quote_valid','quote_timestamp_us','bid_int',
    'ask_int','bid_size','ask_size','price_valid','close_int'))


def _hash(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('Terminal accounting evidence hash is invalid')


def _clock(value):
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Terminal accounting clock must be aware')
    return value.astimezone(timezone.utc)


def terminal_mark_rows_hash(rows):
    """Digest the exact bounded source projection, including row order."""
    return sha256(canonical_json(tuple((at, dict(row)) for at, row in rows)).encode()).hexdigest()


@dataclass(frozen=True)
class TerminalClosingTailScope:
    run_id: str
    first_sequence: int
    last_sequence: int
    start_at: datetime
    stop_at: datetime
    read_tickers: tuple
    market_plan_hash: str
    read_scope_evidence_hash: str
    journal_tail_hash: str
    market_rows_hash: str
    market_row_count: int
    initial_snapshot_hash: str

    def __post_init__(self):
        start, stop = _clock(self.start_at), _clock(self.stop_at)
        if (type(self.run_id) is not str or not self.run_id
                or type(self.first_sequence) is not int or type(self.last_sequence) is not int
                or not 0 < self.first_sequence <= self.last_sequence
                or not 0 < (stop-start).total_seconds() <= 60
                or type(self.read_tickers) is not tuple or not 1 <= len(self.read_tickers) <= 2000
                or any(type(t) is not str or not t or t != t.strip().upper() for t in self.read_tickers)
                or self.read_tickers != tuple(sorted(set(self.read_tickers)))
                or type(self.market_row_count) is not int or not 1 <= self.market_row_count <= 262144):
            raise ValueError('Terminal accounting requires an exact bounded original read scope')
        for value in (self.market_plan_hash,self.read_scope_evidence_hash,
                      self.journal_tail_hash,self.market_rows_hash,self.initial_snapshot_hash):
            _hash(value)


@dataclass(frozen=True)
class RecordedClosingExecution:
    sequence: int
    commission_sequence: int
    execution: Execution
    request: OrderRequest
    content_hash: str
    commission_content_hash: str


@dataclass(frozen=True)
class RecordedClosingAcknowledgement:
    sequence: int
    broker_order_id: str
    request: OrderRequest
    content_hash: str


@dataclass(frozen=True)
class RecordedClosingCancellation:
    sequence: int
    at: datetime
    broker_order_id: str
    account_id: str
    content_hash: str


@dataclass(frozen=True)
class TerminalClosingAccountingResult:
    state: dict
    executions_applied: int
    cancellations_applied: int
    market_rows_observed: int
    untouched_tickers: tuple
    scope: TerminalClosingTailScope
    broker_snapshot: SharedBrokerMatchSnapshot


def rollforward_terminal_broker_accounting(initial_state, *, initial_broker, broker_contract, config, scope,
        market_rows, executions, acknowledgements, cancellations):
    """Apply only independently verified recorded closing facts and marks.

    ``read_tickers`` is the original cohort's certified scope, not the full
    broker cache population. Sparse source rows must be complete within that
    scope and pinned by the caller; this function cannot establish source
    completeness or grant live Keeper ownership from a supplied digest.
    """
    if (type(config) is not SimulationConfig or config.liquidity_resolution_ms != 100
            or type(scope) is not TerminalClosingTailScope or not isinstance(initial_state,Mapping)
            or initial_state.get('schema_version') != 4 or not initial_state.get('bar_mode')
            or type(market_rows) is not tuple or len(market_rows) != scope.market_row_count
            or terminal_mark_rows_hash(market_rows) != scope.market_rows_hash
            or type(executions) is not tuple or not 1 <= len(executions) <= 100000
            or type(acknowledgements) is not tuple or len(acknowledgements) > 100000
            or type(cancellations) is not tuple or len(cancellations) > 100000
            or any(type(x) is not RecordedClosingExecution for x in executions)
            or any(type(x) is not RecordedClosingAcknowledgement for x in acknowledgements)
            or any(type(x) is not RecordedClosingCancellation for x in cancellations)):
        raise ValueError('Terminal accounting lacks its bounded native inputs')
    if type(initial_broker) is not SharedBrokerMatchSnapshot or type(broker_contract) is not BacktestBrokerContract:
        raise ValueError('Terminal accounting requires its verified native initial snapshot and broker contract')
    verified = verify_shared_broker_snapshot(initial_broker, broker_contract=broker_contract,
        run_id=scope.run_id, checkpoint_sequence=scope.first_sequence-1,
        expected_content_hash=scope.initial_snapshot_hash)
    if market_day_boundary(verified.snapshot['session_date'],verified.snapshot['boundary_ms']) != scope.start_at:
        raise ValueError('Terminal accounting start differs from its attested snapshot clock')
    projected = project_shared_broker_snapshot(run_id=scope.run_id,
        session_date=date.fromisoformat(verified.snapshot['session_date']),checkpoint_sequence=scope.first_sequence-1,
        boundary_ms=verified.snapshot['boundary_ms'],state=initial_state,
        simulation_config=config,broker_contract=broker_contract)
    if projected != verified:
        raise ValueError('Initial accounting image differs from its verified broker snapshot')
    original = deepcopy(dict(initial_state))
    broker = SimulatedBrokerAdapter(list(original['account_ids']),config,
        fixed_bar_mode=True,mode=TradingMode.BACKTEST)
    broker.restore_checkpoint_state(original)
    # Normalized cold recovery constructs minimal QuoteEvent payloads. Native
    # restore/checkpoint adds dataclass defaults (sequence/tape) and normalizes
    # aware clocks. Preserve the exact native cache image, while independently
    # proving that this representation still projects to the attested snapshot.
    baseline = broker.checkpoint_state()
    restored = project_shared_broker_snapshot(run_id=scope.run_id,
        session_date=date.fromisoformat(verified.snapshot['session_date']),
        checkpoint_sequence=scope.first_sequence-1,
        boundary_ms=verified.snapshot['boundary_ms'],state=baseline,
        simulation_config=config,broker_contract=broker_contract)
    if restored != verified:
        raise ValueError('Native restored accounting image differs from its verified broker snapshot')
    start, stop = _clock(scope.start_at), _clock(scope.stop_at)
    if ({e.execution_id for e in broker._executions} != {f'SIM-{i}' for i in range(1,broker._next_execution_id)}
            or len(broker._executions) != broker._next_execution_id-1):
        raise ValueError('Initial execution cache does not cover its exact counter')
    if (not broker._performance.get('complete') or broker._next_order_id < 1
            or broker._next_execution_id < 1 or any(at > start for at in broker._bar_boundaries.values())):
        raise ValueError('Terminal accounting initial checkpoint exceeds its certified anchor')
    opening = {(account,p.conid):p.quantity for account,rows in broker._positions.items()
               for p in rows.values() if p.quantity}
    if (not opening or any(quantity <= 0 for quantity in opening.values())
            or any(state.request.side.upper() != 'SELL' or (state.request.acctId,state.request.conid) not in opening
                   for state in broker._orders.values() if state.status in OPEN_ORDER_STATUSES)):
        raise ValueError('Terminal accounting supports existing positive closing books only')
    active_tickers = {p.ticker.upper() for account,rows in broker._positions.items()
                      for p in rows.values() if p.quantity}
    if not active_tickers <= set(scope.read_tickers):
        raise ValueError('Terminal accounting original scope omits a held position')
    old_ids = {identity for identity,state in broker._orders.items() if state.status in OPEN_ORDER_STATUSES}
    cancelled = {x.broker_order_id for x in cancellations}
    if len(cancelled) != len(cancellations) or not cancelled <= old_ids:
        raise ValueError('Terminal cancellation identity repeats or lacks an old order')
    sequences = []
    for x in (*executions,*acknowledgements,*cancellations):
        _hash(x.content_hash)
        if type(x.sequence) is not int or not scope.first_sequence <= x.sequence <= scope.last_sequence:
            raise ValueError('Recorded fact leaves its sealed journal tail')
        sequences.append(x.sequence)
    for x in executions:
        _hash(x.commission_content_hash)
        if type(x.commission_sequence) is not int or not x.sequence < x.commission_sequence <= scope.last_sequence:
            raise ValueError('Execution lacks its later final commission')
        sequences.append(x.commission_sequence)
    if len(sequences) != len(set(sequences)):
        raise ValueError('Terminal recorded facts repeat journal sequences')
    acknowledgements = tuple(sorted(acknowledgements,key=lambda x:x.sequence))
    accepted = {}
    for offset,x in enumerate(acknowledgements):
        if (type(x.broker_order_id) is not str or x.broker_order_id != str(broker._next_order_id+offset)
                or x.broker_order_id in accepted or type(x.request) is not OrderRequest):
            raise ValueError('Recorded broker acknowledgements are not contiguous')
        accepted[x.broker_order_id] = x
    by_id = {};previous_id = broker._next_execution_id-1
    executions = tuple(sorted(executions,key=lambda x:x.sequence))
    previous_clock = start
    for x in executions:
        e,r = x.execution,x.request
        if (type(e) is not Execution or type(r) is not OrderRequest
                or e.execution_id != 'SIM-'+str(previous_id+1)
                or e.side != 'S' or r.side.upper() != 'SELL'
                or e.order_ref != r.cOID or e.account != r.acctId or e.conid != r.conid
                or e.symbol != r.ticker or (e.account,e.conid) not in opening
                or r.raw.get('canonical_run_id') != scope.run_id
                or r.ticker != broker._positions[e.account][e.conid].ticker
                or r.cashQty is not None
                or any(type(v) not in (int,float) or not isfinite(v) or v <= 0 for v in (e.size,e.price))
                or type(e.commission) not in (int,float) or not isfinite(e.commission) or e.commission < 0
                or e.currency != config.base_currency
                or not previous_clock <= _clock(e.trade_time) <= stop):
            raise ValueError('Recorded closing execution differs from its exact request/clock')
        previous_id += 1;previous_clock = _clock(e.trade_time)
        if e.order_id in accepted:
            ack = accepted[e.order_id]
            if ack.request != r or ack.sequence >= x.sequence:
                raise ValueError('Closing execution precedes or differs from acknowledgement')
        elif e.order_id not in old_ids or broker._orders[e.order_id].request != r:
            raise ValueError('Closing execution lacks an old or accepted exact order')
        by_id.setdefault(e.order_id,[]).append(x)
    if [(x.execution.trade_time,x.execution.symbol) for x in executions] != sorted((x.execution.trade_time,x.execution.symbol) for x in executions):
        raise ValueError('Recorded execution sequence differs from native boundary/ticker order')
    if set(accepted) - set(by_id) or old_ids - cancelled - set(by_id):
        raise ValueError('Terminal accounting leaves an unproved broker order')
    for order_id, fills in by_id.items():
        if order_id in cancelled:
            raise ValueError('Terminal order has mixed cancel/fill authority')
        request = fills[0].request
        remaining = (float(request.quantity or 0) if order_id in accepted
                     else broker._orders[order_id].remaining)
        if (remaining <= 0 or sum(float(x.execution.size) for x in fills) != remaining
                or any(x.request != request for x in fills)):
            raise ValueError('Terminal closing order is not completely filled')
    for x in cancellations:
        state = broker._orders[x.broker_order_id]
        if (x.account_id != state.request.acctId or not start <= _clock(x.at) <= stop
                or any(_clock(x.at) > _clock(f.execution.trade_time) for f in executions
                       if f.execution.account==x.account_id and f.execution.conid==state.request.conid)):
            raise ValueError('Old protective cancellation is later than its closing execution')
    # Exact original boundary/ticker order. No missing observed row is inferred.
    rows = [];previous_key = None
    for at,row in market_rows:
        at = _clock(at)
        if (not isinstance(row,Mapping) or set(row) != MARK_COLUMNS
                or row['ticker'] not in scope.read_tickers or not start < at <= stop):
            raise ValueError('Accounting market row leaves its original scope/window')
        key = (at,row['ticker'])
        if previous_key is not None and key <= previous_key:
            raise ValueError('Accounting market rows repeat or change native order')
        previous_key = key;rows.append((at,row))
    matches = {(at,row['ticker']) for at,row in rows}
    if any((_clock(x.execution.trade_time),x.execution.symbol) not in matches for x in executions):
        raise ValueError('Recorded execution lacks its exact certified mark boundary')
    fill_groups = {}
    for x in executions:fill_groups.setdefault((_clock(x.execution.trade_time),x.execution.symbol),[]).append(x)
    cancels = iter(sorted(cancellations,key=lambda x:(_clock(x.at),x.sequence)))
    next_cancel = next(cancels,None)
    for at,row in rows:
        while next_cancel is not None and _clock(next_cancel.at) <= at:
            state = broker._orders[next_cancel.broker_order_id]
            state.status = OrderStatus.CANCELLED
            next_cancel = next(cancels,None)
        broker.observe_liquidity_mark(row,at=at)
        for x in fill_groups.get((at,row['ticker']),()):
            e,r = x.execution,x.request
            position = broker._positions[e.account][e.conid]
            if not 0 < float(e.size) <= position.quantity:
                raise ValueError('Recorded closing execution would open or reverse a position')
            broker._book_execution(r,float(e.price),float(e.size),float(e.commission))
            broker._observe_performance(e.conid,e.trade_time)
            broker._executions.append(deepcopy(e));broker._next_execution_id += 1
            if e.order_id in old_ids:
                broker._orders[e.order_id].status = OrderStatus.FILLED
    while next_cancel is not None:
        broker._orders[next_cancel.broker_order_id].status = OrderStatus.CANCELLED
        next_cancel = next(cancels,None)
    broker._next_order_id += len(acknowledgements)
    if (any(p.quantity for positions in broker._positions.values() for p in positions.values())
            or any(state.status not in {OrderStatus.FILLED,OrderStatus.CANCELLED} for state in broker._orders.values())
            or broker._performance_marks or broker._performance['unrealized'] or broker._performance['market_value']):
        raise ValueError('Terminal accounting did not prove a flat closed book')
    state = broker.checkpoint_state()
    # The shared normalized checkpoint intentionally drops expired liquidity
    # consumption; it is never reused to match a completed boundary.
    state['liquidity_consumed'] = {}
    read_tickers = set(scope.read_tickers)
    untouched = tuple(sorted(set(baseline['bar_boundaries'])-read_tickers))
    for name in ('bar_boundaries','bar_marks_by_ticker','quotes_by_ticker'):
        before = {ticker:value for ticker,value in baseline[name].items() if ticker not in read_tickers}
        after = {ticker:value for ticker,value in state[name].items() if ticker not in read_tickers}
        if canonical_json(after) != canonical_json(before):
            raise ValueError('Terminal accounting altered an out-of-scope ticker cache')
    boundary_ms = int((_clock(scope.stop_at)-market_day_boundary(verified.snapshot['session_date'],0)).total_seconds()*1000)
    final = project_shared_broker_snapshot(run_id=scope.run_id,session_date=date.fromisoformat(verified.snapshot['session_date']),
        checkpoint_sequence=scope.last_sequence,boundary_ms=boundary_ms,state=state,
        simulation_config=config,broker_contract=broker_contract)
    return TerminalClosingAccountingResult(state,len(executions),len(cancellations),len(rows),untouched,scope,final)
