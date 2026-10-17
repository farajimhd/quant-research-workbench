"""Accounting-only continuation must equal the real inert simulator path."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import pytest
from tests.test_simulated_broker_liquidity_bar import START,bar
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter,SimulationConfig
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.domain import TradingMode
from src.trading_runtime.arte_broker_match_snapshot import project_shared_broker_snapshot
from src.trading_runtime.backtest_broker_contract import BacktestBrokerContract,project_broker_contract
from src.backend.backtest_market_data import market_day_boundary
from src.backend import program_terminal_financial_rollforward as m


def request(ticker,conid,side,quantity,coid,**kw):
    return OrderRequest(acctId='DU1',conid=conid,orderType='MKT',side=side,quantity=quantity,
        cOID=coid,ticker=ticker,raw={'canonical_run_id':'run'},**kw)


def source_row(ticker,at,price):
    row=dict(bar(at,bid=price,ask=price+.01,low=price-.01,high=price+.02),ticker=ticker)
    row['first_event_us']=row['last_event_us']-100
    return row


async def actual_case(mark_variant=None):
    config=SimulationConfig(initial_cash=100000.)
    broker=SimulatedBrokerAdapter(['DU1'],config,initial_time=START,fixed_bar_mode=True,mode=TradingMode.BACKTEST)
    await broker.initialize()
    # A third cache belongs to the historical population, outside this tail's
    # exact original read scope; it must never be advanced by recovery.
    await broker.on_liquidity_bar(source_row('ZZZ',START,20.),at=START)
    for ticker,conid,qty in [('AAA',1,10.),('BBB',2,20.)]:
        await broker.place_orders('DU1',[request(ticker,conid,'BUY',qty,'buy'+ticker)])
    opening_at=START+timedelta(milliseconds=100)
    for ticker in ('AAA','BBB'):
        await broker.on_liquidity_bar(source_row(ticker,opening_at,10.),at=opening_at)
    stopids=[]
    for ticker,conid,qty in [('AAA',1,10.),('BBB',2,20.)]:
        stop=request(ticker,conid,'SELL',qty,'stop'+ticker)
        stop=replace(stop,orderType='STP',auxPrice=1.)
        ack=await broker.place_orders('DU1',[stop]);stopids.append(str(ack[0]['order_id']))
    anchor=opening_at
    old=deepcopy(broker.checkpoint_state())
    for identity in stopids:await broker.cancel_order('DU1',identity)
    acks=[];requests=[]
    for ticker,conid,qty in [('AAA',1,10.),('BBB',2,20.)]:
        r=request(ticker,conid,'SELL',qty,'exit'+ticker)
        ack=await broker.place_orders('DU1',[r]);requests.append(r)
        acks.append(m.RecordedClosingAcknowledgement(102+len(acks),str(ack[0]['order_id']),r,'a'*64))
    rows=[];fills=[]
    # Rise then fall before the actual close: endpoint-only accounting would
    # lose the peak/drawdown, which the full checkpoint comparison detects.
    for offset,price in [(2,12.),(3,8.),(4,11.)]:
        at=START+timedelta(milliseconds=offset*100)
        for ticker in ('AAA','BBB'):
            row=source_row(ticker,at,price)
            if offset==2 and mark_variant=='invalid':row.update(quote_valid=0,price_valid=0,close_int=0)
            if offset==2 and mark_variant=='stale':row['quote_timestamp_us']-=2000000
            rows.append((at,{k:row[k] for k in m.MARK_COLUMNS}))
            # Delay exits until the third observed boundary.
            if offset<4:
                for state in broker._orders.values():
                    if state.request.cOID.startswith('exit'):state.submitted_at=at+timedelta(milliseconds=100)
            else:
                for state in broker._orders.values():
                    if state.request.cOID.startswith('exit'):state.submitted_at=at-timedelta(milliseconds=100)
            got=await broker.on_liquidity_bar(row,at=at)
            for e in got:
                i=len(fills);fills.append(m.RecordedClosingExecution(110+i*2,111+i*2,e,next(r for r in requests if r.cOID==e.order_ref),'b'*64,'c'*64))
    assert len(fills)==2
    expected=broker.checkpoint_state();expected['liquidity_consumed']={}
    cancellations=tuple(m.RecordedClosingCancellation(100+i,anchor,identity,'DU1','d'*64) for i,identity in enumerate(stopids))
    rows=tuple(rows)
    contract=BacktestBrokerContract(project_broker_contract('run',config,liquidity_plan_hash='e'*64),config.initial_cash)
    initial_broker=project_shared_broker_snapshot(run_id='run',session_date=anchor.date(),checkpoint_sequence=99,
        boundary_ms=int((anchor-market_day_boundary(anchor.date(),0)).total_seconds()*1000),state=old,simulation_config=config,broker_contract=contract)
    scope=m.TerminalClosingTailScope('run',100,200,anchor,START+timedelta(milliseconds=400),('AAA','BBB'),'e'*64,'f'*64,'1'*64,m.terminal_mark_rows_hash(rows),len(rows),initial_broker.snapshot['content_hash'])
    return config,old,scope,rows,tuple(fills),tuple(acks),cancellations,expected,initial_broker,contract


def inputs(mark_variant=None):return asyncio.run(actual_case(mark_variant))


def project(values):
    config,old,scope,rows,fills,acks,cancels,_,initial_broker,contract=values
    return m.rollforward_terminal_broker_accounting(old,initial_broker=initial_broker,broker_contract=contract,config=config,scope=scope,
        market_rows=rows,executions=fills,acknowledgements=acks,cancellations=cancels)


def test_real_simulator_terminal_parity_all_fp64_caches_counters_and_extrema(monkeypatch):
    values=inputs();before=deepcopy(values[1])
    def forbidden(*a,**kw):raise AssertionError('Matching or order execution invoked')
    monkeypatch.setattr(SimulatedBrokerAdapter,'on_liquidity_bar',forbidden)
    monkeypatch.setattr(SimulatedBrokerAdapter,'_apply_fill',forbidden)
    monkeypatch.setattr(SimulatedBrokerAdapter,'place_orders',forbidden)
    result=project(values)
    expected=dict(values[7]);actual=dict(result.state)
    expected.pop('orders');actual.pop('orders')
    assert actual==expected
    native_snapshot=project_shared_broker_snapshot(run_id='run',session_date=values[2].stop_at.date(),checkpoint_sequence=values[2].last_sequence,
        boundary_ms=int((values[2].stop_at-market_day_boundary(values[2].stop_at.date(),0)).total_seconds()*1000),state=values[7],simulation_config=values[0],broker_contract=values[9])
    assert result.broker_snapshot==native_snapshot
    assert result.untouched_tickers==('ZZZ',)
    assert result.executions_applied==2 and result.cancellations_applied==2
    assert values[1]==before


@pytest.mark.parametrize('change',['missingrow','order','hash','scope','duplicatefill','counter','open','fee','foreign','cancel','start','partial'])
def test_terminal_rollforward_rejects_unproved_or_nonterminal_inputs(change):
    values=list(inputs());config,old,scope,rows,fills,acks,cancels,_,initial_broker,contract=values
    if change=='missingrow':values[3]=rows[:-1];values[2]=replace(scope,market_rows_hash=m.terminal_mark_rows_hash(rows[:-1]),market_row_count=len(rows)-1)
    if change=='order':values[3]=tuple(reversed(rows));values[2]=replace(scope,market_rows_hash=m.terminal_mark_rows_hash(values[3]))
    if change=='hash':values[2]=replace(scope,market_rows_hash='0'*64)
    if change=='scope':values[2]=replace(scope,read_tickers=('AAA',))
    if change=='duplicatefill':values[4]=fills+fills[:1]
    if change=='counter':values[5]=(replace(acks[0],broker_order_id='999'),acks[1])
    if change=='open':values[6]=cancels[:1]
    if change=='fee':values[4]=(replace(fills[0],commission_sequence=fills[0].sequence),fills[1])
    if change=='foreign':values[4]=(replace(fills[0],request=replace(fills[0].request,cOID='other')),fills[1])
    if change=='cancel':values[6]=(replace(cancels[0],at=scope.stop_at+timedelta(seconds=1)),cancels[1])
    if change=='start':values[2]=replace(scope,start_at=scope.start_at-timedelta(seconds=1))
    if change=='partial':values[4]=(replace(fills[0],execution=replace(fills[0].execution,size=1.)),fills[1])
    with pytest.raises((ValueError,KeyError)):project(values)


def cold_quote_values():
    """Exact quote DTO shape returned by reconstruct_broker_match_state."""
    from src.backend.backtest_v4_broker_state_restore import reconstruct_broker_match_state
    from src.backend.backtest_v4_broker_quote_restore import CompletedBrokerQuote
    from src.trading_runtime.arte_broker_match_snapshot import _codec_rows
    from src.trading_runtime.ibkr_schema import OrderStatus
    values=list(inputs())
    old=values[1]
    requests={row['order_id']:OrderRequest.from_cpapi(row['request']) for row in old['orders']
              if OrderStatus(row['status']) in m.OPEN_ORDER_STATUSES}
    quotes={}
    for row in values[8].tickers:
        if row['has_quote']:
            quote=old['quotes_by_ticker'][row['ticker']]
            quotes[row['ticker']]=CompletedBrokerQuote(row['ticker'],row['last_boundary_ms'],
                row['quote_timestamp_us'],quote['bid_price'],quote['ask_price'],
                quote['bid_size'],quote['ask_size'])
    restored=reconstruct_broker_match_state(_codec_rows(values[8]),
        requests_by_broker_id=requests,quotes=quotes)
    restored['executions']=deepcopy(old['executions'])
    values[1]=restored
    return values


def test_cold_reconstructed_quotes_preserve_native_defaults_and_exact_snapshot(monkeypatch):
    values=cold_quote_values();before=deepcopy(values[1])
    assert 'sequence' not in before['quotes_by_ticker']['ZZZ']
    assert 'tape' not in before['quotes_by_ticker']['ZZZ']
    native=project(inputs())
    def forbidden(*a,**kw):raise AssertionError('Matching or order execution invoked')
    monkeypatch.setattr(SimulatedBrokerAdapter,'on_liquidity_bar',forbidden)
    monkeypatch.setattr(SimulatedBrokerAdapter,'_apply_fill',forbidden)
    monkeypatch.setattr(SimulatedBrokerAdapter,'place_orders',forbidden)
    result=project(values)
    assert result.broker_snapshot==native.broker_snapshot
    assert result.state['quotes_by_ticker']['ZZZ']==native.state['quotes_by_ticker']['ZZZ']
    assert result.state['quotes_by_ticker']['ZZZ']['sequence']==0
    assert result.state['quotes_by_ticker']['ZZZ']['tape']==0
    assert values[1]==before


@pytest.mark.parametrize('cache',['quote','mark','boundary','extra','signed_zero'])
def test_native_baseline_still_rejects_actual_out_of_scope_mutations(monkeypatch,cache):
    values=cold_quote_values()
    if cache=='signed_zero':values[1]['quotes_by_ticker']['ZZZ']['bid_size']=0.0
    original=SimulatedBrokerAdapter.observe_liquidity_mark
    def mutate(broker,row,*,at):
        original(broker,row,at=at)
        if cache=='quote':broker._quotes_by_ticker['ZZZ']=replace(broker._quotes_by_ticker['ZZZ'],bid_price=999.)
        if cache=='mark':broker._bar_marks_by_ticker['ZZZ']=999.
        if cache=='boundary':broker._bar_boundaries['ZZZ']=at
        if cache=='extra':broker._bar_marks_by_ticker['FOREIGN']=1.
        if cache=='signed_zero':broker._quotes_by_ticker['ZZZ']=replace(broker._quotes_by_ticker['ZZZ'],bid_size=-0.0)
    monkeypatch.setattr(SimulatedBrokerAdapter,'observe_liquidity_mark',mutate)
    with pytest.raises(ValueError,match='altered an out-of-scope ticker cache'):project(values)


def test_native_baseline_must_reproduce_original_verified_snapshot(monkeypatch):
    values=cold_quote_values()
    original=SimulatedBrokerAdapter.restore_checkpoint_state
    def corrupt(broker,payload):
        original(broker,payload);broker._bar_marks_by_ticker['ZZZ']+=1.
    monkeypatch.setattr(SimulatedBrokerAdapter,'restore_checkpoint_state',corrupt)
    with pytest.raises(ValueError,match='Native restored accounting image differs'):project(values)
