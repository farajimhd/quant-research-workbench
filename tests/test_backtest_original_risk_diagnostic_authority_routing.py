"""Actual producer/routing and cache guards; external receipt/detail seams explicit."""
import os,sys,asyncio,queue,threading
from pathlib import Path
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from uuid import UUID
from hashlib import sha256

import pytest

os.environ['PYTHONDONTWRITEBYTECODE']='1'
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_original_risk_pending_snapshot import genuine_entry_graph
from test_backtest_typed_publisher import FakeWriter,RUN,ATTEMPT,DAY
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
from src.trading_runtime.arte_journal_writer import ArteJournalWriter,_ProfitPublicationUnit


def test_actual_native_diagnostic_compound_drain_forwards_genuine_publisher_authority():
    from src.trading_runtime.confirmed_original_risk_failure import CompletedRiskBucket,OriginalRiskDecisionDiagnostic,CONFIRMED_ORIGINAL_RISK_RULE,ConfirmedOriginalRiskPolicy
    from src.trading_runtime.original_risk_checkpoint import OriginalRiskCheckpointReference
    from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailure
    from src.trading_runtime.strategy_followthrough_exit import followthrough_exit_intent
    from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
    from src.trading_runtime.strategy_engine import AssignmentStatus,StrategyPermissions
    from src.backend.backtest_market_data import market_day_boundary
    async def run():
        proposal,entry,authority,_=genuine_entry_graph(RUN)
        assert authority.entry_activity_source is not None
        journal=BacktestMemoryJournal(run_id=RUN)
        journal.append_strategy_one_intent(intent=entry,proposal=proposal,session_date=DAY,
            account_id=proposal.account_id,strategy_id='early-squeeze-strategy',strategy_revision=68,
            first_price_source=authority)
        captured=[]
        class Writer(FakeWriter):
            journal_profile='backtest_v4'
            def submit_strategy_one_entry_v4(self,unit):return self.submit(unit.base)
            def submit_compound_v4(self,unit,**kwargs):
                # Actual queued writer validation; no network thread or financial sealer.
                target=object.__new__(ArteJournalWriter)
                target._journal_profile='backtest_v4';target._run_id=RUN
                target._client=SimpleNamespace(confirmed_original_risk_policy=ConfirmedOriginalRiskPolicy())
                target._submission_lock=threading.Lock();target._queue=queue.Queue()
                target._closed=False;target._error=None
                ArteJournalWriter.submit_compound_v4(target,unit,**kwargs)
                queued,_=target._queue.get_nowait()
                assert type(queued) is _ProfitPublicationUnit
                captured.append((unit,kwargs,queued.first_price_source))
                raise RuntimeError('controlled writer receipt boundary')
        writer=Writer()
        publisher=BacktestTypedJournalPublisher(journal,writer,attempt_id=ATTEMPT,
            run_month=DAY.replace(day=1),expected_config={'mode':'backtest',
                'strategy_id':'early-squeeze-strategy','strategy_revision':68})
        publisher._first_price_source=authority
        assert (await publisher.enqueue_pending()).last_sequence==1
        financial=StrategyOneFinancialView(proposal.assignment_id,proposal.account_id,proposal.ticker,
            AssignmentStatus.MANAGING,StrategyPermissions(),967.,False,False,False,1)
        witness=FollowThroughFailure(120000,42000,proposal.reference_ask,proposal.initial_stop,
            99800,.1,.2,9.98,9.99,1000)
        newest=CompletedRiskBucket(120000,99800,True,.1,.2,'build','a'*64,
            str(UUID(int=1)),str(UUID(int=2)),DAY.isoformat(),proposal.ticker,str(UUID(int=3)))
        refs=OriginalRiskCheckpointReference(str(UUID(int=11)),1,'a'*64,str(UUID(int=12)),'b'*64)
        diagnostic=OriginalRiskDecisionDiagnostic(witness,newest,replace(newest,boundary_ms=115000),
            CONFIRMED_ORIGINAL_RISK_RULE,refs)
        intent=followthrough_exit_intent(witness,financial,session_date=DAY,
            source_entry_intent_id=entry.intent_id,strategy_number=68,diagnostic=diagnostic)
        journal.append_followthrough_exit(intent=intent,witness=witness,source_entry_intent_id=entry.intent_id,
            account_id=financial.account_id,strategy_id='early-squeeze-strategy',strategy_revision=68,
            assignment_id=financial.assignment_id,diagnostic=diagnostic)
        journal.append(run_id=RUN,category='checkpoint',entity_type='market_boundary',
            entity_id=DAY.isoformat()+':120000',event_time=market_day_boundary(DAY,120000),
            payload={'session_date':DAY.isoformat(),'boundary_ms':120000,'market_sequence':2,
                'frame_as_of':None,'frame_ticker':None,'frame_timeframe':None,'frame_sequence':None})
        with pytest.raises(RuntimeError,match='controlled writer receipt boundary'):
            await publisher.enqueue_pending()
        unit,kwargs,queued_source=captured[0]
        assert unit.children['original_risk_diagnostics']
        assert not any(unit.children[name] for name in ('profit_givebacks','confirmed_ah_failures','liquidity_fade_failures'))
        assert kwargs=={'first_price_source': authority} and queued_source is authority
        assert publisher._first_price_source is authority
        journal.close()
    asyncio.run(run())


def test_actual_warm_cache_keeps_full_genuine_activity_scope_and_rejects_none(monkeypatch):
    from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
    from src.trading_runtime.keeper_session import ManagedKeeperSession
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch,_Gate,_gate_path
    from src.trading_runtime import arte_journal_commit_v4 as commit,arte_journal_writer as writer
    from src.trading_runtime.journal_contract import canonical_json
    from tests.test_live_signal_completion_keeper import FakeKazoo
    _,_,authority,_=genuine_entry_graph(RUN)
    zero=str(UUID(int=0));batch=str(UUID(int=11))
    row=dict(run_id=RUN,run_month='2026-08-01',batch_id=batch,prior_batch_id=zero,
        first_sequence=1,last_sequence=2,event_count=2,status='running',source_cursor='2026-08-18:120000')
    keeper=FakeKazoo();keeper.add_listener=lambda _:None
    session=ManagedKeeperSession(keeper);session._on_state('CONNECTED')
    lease=BacktestV4KeeperLease.acquire(session,run_id=RUN,owner_id='test-only')
    dispatch=TypedInsertDispatch(keeper);dispatch.initialize_new_run(RUN)
    gate,_=dispatch._read_gate(RUN);digest=sha256(canonical_json(row).encode()).hexdigest()
    keeper.nodes[_gate_path(RUN)]=(_Gate('open',0,gate.epoch,0,2,batch,digest,zero).wire(),1,0)
    client=SimpleNamespace(backtest_v4_lease=lease,typed_insert_strict=True,typed_insert_dispatch=dispatch)
    # External committed-detail producer seam; native lease/head/cache guard untouched.
    monkeypatch.setattr(writer,'_rows',lambda *a:[row])
    monkeypatch.setattr(commit,'load_verified_commit_v4',lambda *a,**k:(row,()))
    prefix=commit.load_writer_v4_snapshot_prefix(client,RUN,first_price_source=authority)
    for stage in ('original-risk-checkpoint','manager','broker','profit-arming-checkpoint'):
        assert commit.load_writer_v4_snapshot_prefix(client,RUN,first_price_source=authority) is prefix
    assert client._v4_writer_snapshot_price_scope==(RUN,authority.plan.token,
        authority.plan.source.token,authority.entry_activity_source.plan.token)
    with pytest.raises(RuntimeError,match='cached price authority differs'):
        commit.load_writer_v4_snapshot_prefix(client,RUN)
