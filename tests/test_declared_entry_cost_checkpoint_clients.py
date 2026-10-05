"""Real controller confirmations create exact declared SELECT-only readers."""
import asyncio
from threading import get_ident
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from test_profit_arming_engine_boundary import manager_fixture,reference
from src.backend.replay_run_service import ReplayRunController,ReplayRunService
from src.backend.backtest_typed_publisher import TypedBacktestReceipt
from src.trading_runtime.runtime import RunMode


def clients(monkeypatch):
    from research.mlops import clickhouse
    for stem,user in [('BACKTEST_V4_RUNNER','backtest_v4_runner'),('BACKTEST_V4_ENTRY_COST_RUNNER','backtest_v4_entry_cost_runner'),('BACKTEST_V4_LADDER_RUNNER','backtest_v4_ladder_runner')]:
        monkeypatch.delenv(stem+'_CREDENTIAL_FILE',raising=False)
        for suffix,value in [('URL','http://127.0.0.1:8123'),('USER',user),('PASSWORD','test-only')]:
            monkeypatch.setenv(stem+'_CLICKHOUSE_'+suffix,value)
    made=[]
    class Client:
        def __init__(self,url,user,password,**kwargs):
            self.user=user;self.options=kwargs;self.closed=False;made.append(self)
        def close(self):self.closed=True
    monkeypatch.setattr(clickhouse,'ClickHouseHttpClient',Client)
    return made


@pytest.mark.parametrize('number',[42,50,57,58])
@pytest.mark.parametrize('failure',[False,True])
@pytest.mark.parametrize('lane',['profit','liquidity'])
def test_real_confirmations_select_declared_principal_and_close(monkeypatch,number,failure,lane):
    from src.trading_runtime import strategy_one_management_snapshot as snapshots
    from src.trading_runtime import strategy_one_broker_match_snapshot as broker_snapshots
    from src.trading_runtime import strategy_profit_giveback_arm_reference as arms
    from src.trading_runtime import strategy_liquidity_fade_checkpoint_reference as liquidity
    from src.backend import backtest_strategy_one_financial as financials
    made=clients(monkeypatch);manager,financial,state=manager_fixture(strategy_number=number)
    c=object.__new__(ReplayRunController);c.definition=SimpleNamespace(mode=RunMode.BACKTEST);c.run_id='run'
    c._strategy_one_manager=manager;c._journal_publisher=SimpleNamespace(writer=SimpleNamespace(journal_profile='backtest_v4'),_first_price_source='source')
    c._fixed_keeper_session=object();c._source_cursor={'boundary_ms':state.boundary_ms};c._record_stage_time=lambda *a:None
    calls=[];receipt=TypedBacktestReceipt(20,'batch','cursor')
    async def fence(at):calls.append('fenced');return receipt
    c._save_restart_checkpoint_responsive=fence
    monkeypatch.setattr(snapshots,'ManagedManagerSnapshotHeadReader',lambda x:x)
    monkeypatch.setattr(broker_snapshots,'ManagedBrokerMatchHeadReader',lambda x:x)
    thread=get_ident();expected='backtest_v4_entry_cost_runner'if number in (57,58)else 'backtest_v4_runner'
    def checked(reader):
        assert calls==['fenced'] and get_ident()!=thread
        assert reader.user==expected and reader.options['default_query_params']['readonly']==1
        assert reader.entry_spread_risk_profile==(number in (57,58))
        assert not reader.automatic_ladder_profile
        calls.append('confirmed')
        if failure:raise ValueError('confirmation failed')
    if lane=='profit':
        requests=manager.profit_arming_requests(boundary_ms=state.boundary_ms)
        def confirm(reader,keeper,candidate,view,r,**kwargs):
            checked(reader);assert r is receipt;return reference(candidate)
        monkeypatch.setattr(arms,'confirm_profit_arm_reference',confirm)
        action=c._confirm_profit_arming_checkpoint(requests,event_time='at')
    else:
        request=SimpleNamespace(financial=financial,witness=object(),source_entry_intent_id='intent')
        requests=(request,)
        monkeypatch.setattr(manager,'liquidity_fade_requests',lambda **kw:requests)
        monkeypatch.setattr(manager,'complete_liquidity_fade_requests',lambda *a,**kw:calls.append('completed'))
        assignment=SimpleNamespace(account_id=financial.account_id,assignment_id=financial.assignment_id,ticker=financial.ticker)
        c._runtime=SimpleNamespace(strategy=SimpleNamespace(assignments=lambda:(assignment,)),broker=object(),order_manager=object(),submit_liquidity_fade_failure=AsyncMock())
        monkeypatch.setattr(financials,'read_strategy_one_financial_view',AsyncMock(return_value=financial))
        def confirm(reader,*args,**kwargs):checked(reader);return ({'exact':'source'},)
        monkeypatch.setattr(liquidity,'confirm_liquidity_fade_checkpoint_sources',confirm)
        action=c._confirm_liquidity_fade_checkpoint(requests,event_time='at')
    if failure:
        with pytest.raises(ValueError,match='confirmation failed'):asyncio.run(action)
    else:asyncio.run(action)
    assert len(made)==1 and made[0].closed
    if lane=='liquidity':assert c._runtime.submit_liquidity_fade_failure.await_count==(0 if failure else 1)


@pytest.mark.parametrize('number',[42,49,51,57,58])
def test_real_resume_assembly_propagates_profile_to_readers_and_writer(monkeypatch,number,tmp_path):
    from src.backend import replay_run_service as replay
    from src.trading_runtime import keeper_session
    from src.backend import backtest_v4_keeper_lease as leases
    from src.trading_runtime import arte_journal_writer as writers
    from src.backend import backtest_strategy_one_configuration as config_module
    made=clients(monkeypatch)
    credential_calls=[]
    original_credentials=writers._v4_runner_credentials
    def credentials(**options):
        resolved=original_credentials(**options)
        credential_calls.append((options,resolved[1]))
        return resolved
    monkeypatch.setattr(writers,'_v4_runner_credentials',credentials)
    config={'strategy':{'strategy_id':'early-squeeze-strategy','strategy_number':number}}
    if number in (49,51):
        from src.trading_runtime.squeeze_ladder_automatic import AutomaticLadderPolicy
        config['strategy']['numbered_release']={'automatic_entry_policy':AutomaticLadderPolicy().payload()}
    definition=SimpleNamespace(mode=RunMode.BACKTEST,configuration_revision={'payload':config,'content_hash':'a'*64})
    monkeypatch.setattr(replay,'_backtest_launch_blocker',lambda _:None)
    monkeypatch.setattr(replay.ReplayRunController,'__init__',lambda self,*a,**kw:None)
    monkeypatch.setattr(replay.ReplayRunController,'_fixed_strategy_one_plans',AsyncMock(return_value=SimpleNamespace(market=object())))
    # Plan/profile acquisition is outside this client propagation regression.
    from src.backend import backtest_v4_run_context as profiles
    from src.backend import backtest_journal_clickhouse as code
    from src.backend import backtest_v3_clients as markets
    monkeypatch.setattr(profiles,'historical_strategy_one_portfolio_profiles',lambda c:((SimpleNamespace(account_id='DU1'),),()))
    monkeypatch.setattr(code,'backtest_code_hash',lambda *a:'b'*64)
    monkeypatch.setattr(markets,'v3_client',lambda *a:SimpleNamespace(close=lambda:None))
    closed=[];keeper=SimpleNamespace(close=lambda:closed.append('keeper'))
    monkeypatch.setattr(keeper_session,'open_workstation_keeper_session',lambda:keeper)
    lease=SimpleNamespace(release=lambda:closed.append('lease'))
    monkeypatch.setattr(leases.BacktestV4KeeperLease,'acquire',lambda *a,**kw:lease)
    # Real writer factory resolves declared credentials before checking writable
    # Keeper authority. Deliberately invalid Keeper stops before any DB/writer.
    service=object.__new__(ReplayRunService);service.runtime_root=tmp_path
    with pytest.raises(RuntimeError,match='caller-owned writable Keeper'):
        asyncio.run(service._prepare_typed_v4_resume('00000000-0000-0000-0000-000000000001',definition))
    expected='backtest_v4_ladder_runner'if number in(49,51)else 'backtest_v4_entry_cost_runner'if number in(57,58)else 'backtest_v4_runner'
    assert len(made)==2 and all(c.user==expected and c.closed for c in made)
    assert all(c.entry_spread_risk_profile==(number in(57,58))for c in made)
    assert closed==['lease','keeper']
    assert all(c.automatic_ladder_profile==(number in(49,51))for c in made)
    assert len(credential_calls)==3
    assert all(user==expected and options['entry_spread_risk']==(number in(57,58))for options,user in credential_calls)
