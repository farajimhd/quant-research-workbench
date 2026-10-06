"""Actual typed products, structural links only; no committed authority fixture."""
from dataclasses import replace
from datetime import date
from hashlib import sha256
from types import MappingProxyType
import pytest
from src.backend.backtest_market_data import market_day_boundary
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.arte_journal_writer import _canonical_typed_content
from src.trading_runtime.arte_portfolio_snapshot import prepare_portfolio_snapshot,_snapshot_rows,_state_hash
from src.trading_runtime.strategy_one_broker_match_snapshot import project_broker_match_snapshot
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.arte_running_financial_checkpoint import project_running_financial_checkpoint,TABLES

RUN='11111111-1111-4111-8111-111111111111'
BATCH='22222222-2222-4222-8222-222222222222'

def case(accounts=('a','b')):
    day=date(2026,8,4); boundary=1000; at=market_day_boundary(day,boundary)
    prefix=V4CommittedPrefix(RUN,7,BATCH,'original-cursor','running',(BATCH,))
    cursor=dict(record_id='33333333-3333-4333-8333-333333333333',run_id=RUN,
        event_month=day.replace(day=1).isoformat(),batch_id=BATCH,account_id='',
        session_date=day.isoformat(),boundary_ms=boundary,market_sequence=4,
        frame_as_of=None,frame_ticker=None,frame_timeframe=None,frame_sequence=None)
    cursor['content_hash']=sha256(canonical_json(_canonical_typed_content(
        'trading_backtest_cursor_v1',cursor,stored_utc=True)).encode()).hexdigest()
    cursor['event_sequence']=7
    state=dict(schema_version=4,bar_mode=True,initial_time=market_day_boundary(day,0),
        account_ids=accounts,cash={a:10000 for a in accounts},realized_pnl={a:0 for a in accounts},
        positions={a:[] for a in accounts},orders=(),next_order_id=1,next_execution_id=1,
        performance_extrema=dict(complete=False,as_of=None,unrealized=0,market_value=0,
            peak_unrealized=0,worst_unrealized=0,equity_peak=0,maximum_drawdown=0))
    broker=project_broker_match_snapshot(run_id=RUN,session_date=day,checkpoint_sequence=7,
        boundary_ms=boundary,state=state)
    portfolios=tuple(prepare_portfolio_snapshot(run_id=RUN,account_id=a,state_revision=7,
        snapshot_at=at,state=dict(account_key=a,control_mode='enabled',sync_state='synchronized',
        snapshot_id='broker-source',observed_at=at,stale_reason='',peak_net_liquidation=10000,
        realized_pnl_baseline=None,selected_policy=None,disabled_strategy_allocations=[],
        pending_operational_commands=[],pending_entry_requests={},reservations=[],
        allocations=[],reconciliation=[])) for a in accounts)
    return dict(prefix=prefix,cursor=cursor,configuration_hash='a'*64,portfolios=portfolios,broker=broker)

def test_real_typed_products_exact_links_and_determinism():
    args=case(); rows=project_running_financial_checkpoint(**args)
    assert rows == project_running_financial_checkpoint(**args)
    assert rows.root['source_cursor']=='original-cursor'
    assert rows.root['broker_snapshot_hash']==args['broker'].snapshot['content_hash']
    for i,p in enumerate(args['portfolios']):
        assert rows.accounts[i]['state_hash']==_state_hash(_snapshot_rows(
            p.run_id,p.account_id,p.state_revision,p.snapshot_month,p.rows))
        assert set(rows.accounts[i])=={n for n,_ in TABLES[1].columns}
    assert set(rows.root)=={n for n,_ in TABLES[0].columns}
    assert all("storage_policy = 'live_market_ssd'" in t.ddl() for t in TABLES)
    with pytest.raises(TypeError): rows.root['last_sequence']=8
    with pytest.raises(TypeError): rows.accounts[0]['state_hash']='b'*64

@pytest.mark.parametrize('change',[
    lambda a:a.update(prefix=replace(a['prefix'],status='completed')),
    lambda a:a.update(prefix=replace(a['prefix'],last_sequence=True)),
    lambda a:a.update(prefix=replace(a['prefix'],batch_ids=())),
    lambda a:a.update(prefix=replace(a['prefix'],run_id='foreign')),
    lambda a:a.update(configuration_hash='0'*64),
    lambda a:a.update(configuration_hash=4),
    lambda a:a['cursor'].update(event_sequence=7.0),
    lambda a:a['cursor'].update(boundary_ms=True),
    lambda a:a['cursor'].update(batch_id=RUN),
    lambda a:a['cursor'].update(content_hash='b'*64),
    lambda a:a['cursor'].update(extra='unknown'),
    lambda a:a['cursor'].update(session_date=date(2026,8,4)),
    lambda a:a.update(portfolios=a['portfolios'][:1]),
    lambda a:a.update(portfolios=a['portfolios'][::-1]),
    lambda a:a.update(portfolios=(a['portfolios'][0],a['portfolios'][0])),
    lambda a:a.update(portfolios=[*a['portfolios']]),
    lambda a:a.update(portfolios=(replace(a['portfolios'][0],state_revision=8),a['portfolios'][1])),
    lambda a:a.update(portfolios=(replace(a['portfolios'][0],run_id=BATCH),a['portfolios'][1])),
    lambda a:a.update(broker=replace(a['broker'],snapshot={**a['broker'].snapshot,'boundary_ms':1100})),
])
def test_malformed_detached_account_and_clock_graph(change):
    args=case(); change(args)
    with pytest.raises((ValueError,RuntimeError,TypeError)): project_running_financial_checkpoint(**args)

def test_another_valid_broker_clock_rejected_without_corruption():
    a=case(); b=case(accounts=('a',)); a['broker']=b['broker']
    with pytest.raises(ValueError,match='account graph'): project_running_financial_checkpoint(**a)

def test_snapshot_capture_clock_must_equal_cursor():
    a=case(); p=a['portfolios'][0]
    a['portfolios']=(replace(p,rows=replace(p.rows,account=MappingProxyType({**p.rows.account,
        'snapshot_at':'2026-08-04T04:00:00.000000+00:00'}))),a['portfolios'][1])
    with pytest.raises(ValueError,match='Portfolio clock'): project_running_financial_checkpoint(**a)

@pytest.mark.parametrize('mutation',[
    lambda r:replace(r,root=dict(r.root)),
    lambda r:replace(r,accounts=tuple(reversed(r.accounts))),
    lambda r:replace(r,root=MappingProxyType({**r.root,'account_count':True})),
    lambda r:replace(r,root=MappingProxyType({**r.root,'content_hash':'b'*64})),
    lambda r:replace(r,accounts=(MappingProxyType({**r.accounts[0],'ordinal':0.0}),r.accounts[1])),
    lambda r:replace(r,accounts=(MappingProxyType({**r.accounts[0],'state_hash':'c'*64}),r.accounts[1])),
])
def test_result_revalidation_rejects_alias_and_inventory_mutation(mutation):
    rows=project_running_financial_checkpoint(**case())
    rows.__post_init__()
    with pytest.raises(ValueError): mutation(rows)

def test_hash_is_content_not_claim_of_persisted_admission():
    a=case(); first=project_running_financial_checkpoint(**a)
    a['configuration_hash']='b'*64
    second=project_running_financial_checkpoint(**a)
    assert first.root['content_hash'] != second.root['content_hash']
    assert first.accounts == second.accounts
    # No read client, writer lease, Keeper or approval is accepted by this API.

def test_standalone_schema_preserves_original_module_aliases():
    from src.trading_runtime import arte_running_financial_checkpoint as projection
    from src.trading_runtime import arte_running_financial_checkpoint_schema as schema
    assert projection.ROOT is schema.ROOT
    assert projection.ACCOUNT is schema.ACCOUNT
    assert projection.TABLES is schema.TABLES
    assert tuple(t.name for t in schema.TABLES)==(
        'trading_running_financial_checkpoint_v1',
        'trading_running_financial_checkpoint_account_v1')
    assert all("storage_policy = 'live_market_ssd'" in t.ddl() for t in schema.TABLES)

@pytest.mark.parametrize('schema_first',[True,False])
def test_schema_writer_cold_import_order_and_dependency_boundary(schema_first):
    import os,subprocess,sys
    schema='src.trading_runtime.arte_running_financial_checkpoint_schema'
    writer='src.trading_runtime.arte_journal_writer'
    code=("import importlib,sys; importlib.import_module('"+schema+"'); "
          "assert '"+writer+"' not in sys.modules; importlib.import_module('"+writer+"')"
          if schema_first else "import importlib; importlib.import_module('"+writer+"'); "
          "importlib.import_module('"+schema+"')")
    result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True,
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    assert result.returncode==0,result.stderr
