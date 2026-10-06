"""Shared actor fixtures are unregistered; installed hook is TEST-only stand-in."""
import asyncio
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest
from test_backtest_declared_native_fixed_entry import parent, Source, financial, RUN
from test_portfolio_management import summary, ledger
from src.backend.backtest_declared_native_fixed_plan import load_declared_entry_source_plan
from src.backend.backtest_declared_native_fixed_entry import DeclaredEntryPreparation
from src.backend.backtest_declared_native_journal import DeclaredNativeJournal
from src.trading_runtime import declared_native_submission as module
from src.trading_runtime.declared_native_entry_request import inherited_fixed_entry_request_policy, DeclaredEntryRequestPolicy
from src.trading_runtime.runtime import TradingRuntime, RunMode
from src.trading_runtime.signals import StrategyEvaluation
from src.trading_runtime.portfolio import PortfolioManagementEngine, PortfolioAccountProfile, PortfolioPolicy
from src.trading_runtime.order_management import OrderManagementEngine
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
from src.trading_runtime.risk import RiskAuthority
from src.trading_runtime.domain import TradingMode, InstrumentContract
from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner

@pytest.fixture
def command(parent):
 source=load_declared_entry_source_plan(parent,client=Source(parent))
 f=financial(parent);prep=DeclaredEntryPreparation(RUN,f.assignment_id,f.account_id,source)
 binding=module.DeclaredSubmissionBinding(prep,date.fromisoformat(parent.market.sessions[0]),inherited_fixed_entry_request_policy(),'a'*64,'b'*64)
 proposal=prep.propose(f.ticker,31000,f).proposal
 return module.DeclaredNativeSubmission(binding,proposal,f,module.declared_entry_intent(binding,proposal))


def runtime(command):
 rt=TradingRuntime.__new__(TradingRuntime);identity=command.binding.identity
 rt.run_id=RUN;rt.config=SimpleNamespace(mode=RunMode.BACKTEST,strategy_id=identity.strategy_id,
  strategy_revision=identity.revision,anchor_date=command.binding.session_date)
 rt.journal=DeclaredNativeJournal(binding=command.binding,run_id=RUN)
 rt.last_event_time=command.intent.event_time;rt.strategy=SimpleNamespace()
 rt.intent_planner=object();rt.order_manager=object()
 return rt


def test_production_installed_gate_remains_closed_before_side_effects(command):
 rt=runtime(command)
 with pytest.raises(RuntimeError,match='installed source approval'):
  asyncio.run(rt.submit_declared_submission(command))
 assert rt.journal.records(RUN)==[]

@pytest.mark.parametrize('change',['account','intent','clock','legacy_channel','journal'])
def test_malformed_command_rejects_before_installed_gate(command,change,monkeypatch):
 rt=runtime(command);calls=[]
 monkeypatch.setattr(module,'require_installed_submission_binding',lambda _:calls.append('gate'))
 kw=dict(declared_submission=command)
 account=command.account_id;intent=command.intent
 if change=='account':account='foreign'
 if change=='intent':intent=replace(intent,reference_price=intent.reference_price+.01)
 if change=='clock':rt.last_event_time=None
 if change=='legacy_channel':kw['strategy_one_assignment_id']='foreign'
 if change=='journal':rt.journal=object()
 with pytest.raises(ValueError):asyncio.run(rt._execute_intents(StrategyEvaluation(intents=(intent,)),account,None,**kw))
 assert calls==[]


@pytest.mark.parametrize('available',[10000.,0.])
def test_real_shared_portfolio_and_oms_follow_own_source_append_and_refresh(command,monkeypatch,available):
 async def run():
  rt=runtime(command);calls=[];at=command.intent.event_time
  # No production toggle: only this test replaces the unimplemented installed hook.
  monkeypatch.setattr(module,'require_installed_submission_binding',lambda b:b.__post_init__())
  policy=PortfolioPolicy(policy_id='unregistered-actor-fixture',allow_outside_rth=True,
   maximum_position_fraction=1.,maximum_ticker_fraction=1.,maximum_strategy_fraction=1.,
   maximum_planned_risk_fraction=1.,maximum_open_risk_fraction=1.)
  profile=PortfolioAccountProfile('primary',command.account_id,'backtest','cash',policy)
  rt.portfolio=PortfolioManagementEngine((profile,),journal=rt.journal,run_id=RUN,
   strategy_id=rt.config.strategy_id,strategy_revision=rt.config.strategy_revision,event_clock=lambda:at)
  broker=SimulatedBrokerAdapter([command.account_id],mode=TradingMode.BACKTEST,initial_time=at,fixed_bar_mode=True)
  await broker.initialize();risk=RiskAuthority();await risk.prime(broker,[command.account_id])
  instrument=InstrumentContract(command.proposal.ticker,123,command.proposal.ticker,'STK','USD')
  planner=RuntimeIbkrStrategyOrderPlanner({command.proposal.ticker:instrument},
   strategy_id=rt.config.strategy_id,strategy_revision=rt.config.strategy_revision,limit_offset_bps=0.)
  rt.order_manager=OrderManagementEngine(broker=broker,planner=lambda i,a,e:planner.plan(intent=i,account_id=a,event=e),risk=risk,journal=rt.journal,
   run_id=RUN,strategy_id=rt.config.strategy_id,strategy_revision=rt.config.strategy_revision,causal_execution_clock=True)
  rt._canonical_session=None;rt._review_only=False
  async def refresh(*,for_entry_admission=False):
   records=rt.journal.records(RUN);assert records[0].entity_type=='declared_native_intent'
   assert rt.journal.declared_submission_for_record(records[0].record_id)==command
   assert for_entry_admission is True;calls.append('refresh')
   rt.portfolio.synchronize_snapshot(command.account_id,summary=summary(command.account_id,equity=10000,available=available,at=at),ledger=ledger(command.account_id,cash=available,at=at),positions=[])
  rt._refresh_portfolio_from_broker=refresh
  original=rt.portfolio.approve
  async def approve(*args,**kw):
   assert calls==['refresh'];assert kw['assignment_id']==command.assignment_id;calls.append('approve')
   return await original(*args,**kw)
  rt.portfolio.approve=approve
  submit=rt.order_manager.submit_intent
  async def submitted(intent,**kw):
   assert calls==['refresh','approve'];assert intent.quantity>0
   assert intent.metadata['assignment_id']==command.assignment_id;calls.append('oms')
   return await submit(intent,**kw)
  rt.order_manager.submit_intent=submitted
  try:
   result=await rt.submit_declared_submission(command)
   if available:
    assert calls==['refresh','approve','oms'];assert result[0]['decision']['status'] in {'approved','resized'}
    assert rt.portfolio.reservations
   else:
    assert calls==['refresh','approve'];assert result[0]['order_group'] is None
    assert not rt.portfolio.reservations
  finally:
   await rt.order_manager.close()
 asyncio.run(run())


def test_declared_request_fields_match_original_semantic_economics(command):
 from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal
 from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
 p=command.proposal
 # Test-only original schema control; never submitted or used as own source.
 control=StrategyOneEntryProposal(p.assignment_id,p.account_id,p.ticker,p.boundary_ms,p.episode_start_ms,
  p.reference_ask,p.initial_stop,p.initial_target,p.target_level_id,p.frozen_gap,p.bos_break_boundary_ms,p.bos_support_level_id)
 old=strategy_one_entry_intent(control,session_date=command.binding.session_date)
 for field in ('quantity','capital_request','protection_profile','urgency','time_in_force','outside_rth'):
  assert getattr(old,field)==getattr(command.intent,field)
 assert old.execution_policy==replace(command.intent.execution_policy,
  envelope=replace(command.intent.execution_policy.envelope,maximum_buy_price=None))
 assert command.intent.execution_policy.envelope.maximum_buy_price==p.reference_ask
 with pytest.raises(TypeError):command.intent.metadata['authority']=True

@pytest.mark.parametrize('policy',[None,True,{},'foreign'])
def test_binding_cannot_infer_request_policy(command,policy):
 with pytest.raises(ValueError):replace(command.binding,entry_request=policy)


def test_mutated_typed_request_policy_and_rth_clock_reject(command):
 policy=object.__new__(DeclaredEntryRequestPolicy)
 object.__setattr__(policy,'declaration_json','{}')
 with pytest.raises(ValueError):replace(command.binding,entry_request=policy)
 with pytest.raises(ValueError,match='extended acquisition'):
  module.declared_entry_intent(command.binding,replace(command.proposal,boundary_ms=20000000))



def test_actor_source_buffer_failure_precedes_broker_refresh_and_admission(command,monkeypatch):
 rt=runtime(command)
 rt.journal=DeclaredNativeJournal(binding=command.binding,run_id=RUN,max_pending_records=1)
 rt.journal.append(run_id=RUN,category='fixture',entity_type='full',entity_id='full',payload={})
 monkeypatch.setattr(module,'require_installed_submission_binding',lambda b:b.__post_init__())
 calls=[]
 async def refresh(**kwargs):calls.append('refresh')
 rt._refresh_portfolio_from_broker=refresh
 with pytest.raises(RuntimeError,match='buffer is full'):
  asyncio.run(rt.submit_declared_submission(command))
 assert calls==[]
 assert rt.journal.assignment_for_intent(command.intent.intent_id) is None



class _StringAlias(str):
 pass


def test_fenced_companion_retires_without_duplicate_retry_or_changed_source(command):
 journal=DeclaredNativeJournal(binding=command.binding,run_id=RUN,max_pending_records=2)
 def append(value):
  return journal.append_declared_native_intent(submission=value,intent=value.intent,
   account_id=value.account_id,strategy_id=value.binding.identity.strategy_id,
   strategy_revision=value.binding.identity.revision)
 first=append(command)
 tail=journal.append(run_id=RUN,category='fixture',entity_type='tail',entity_id='tail',payload={})
 for invalid in (True,float(first.sequence),tail.sequence+1):
  with pytest.raises(ValueError):journal.mark_fenced(invalid)
  assert journal.declared_submission_for_record(first.record_id)==command
  assert journal.pending_record_count==2
 journal.mark_fenced(first.sequence)
 assert journal.declared_submission_for_record(first.record_id) is None
 assert journal.unfenced_records()==[tail]
 assert append(command)==first
 assert journal.pending_record_count==1
 assert journal.assignment_for_intent(command.intent.intent_id)==command.assignment_id
 with pytest.raises(ValueError,match='retry changed'):
  append(replace(command,financial=replace(command.financial,current_purchase_groups=command.financial.current_purchase_groups+1)))
 journal.close()
 assert not journal._declared_records and not journal._declared_intents
 with pytest.raises(RuntimeError,match='closed'):append(command)

@pytest.mark.parametrize('field,alias',[
 ('strategy_revision','float'),('strategy_revision','bool'),('strategy_revision','string'),
 ('strategy_id','subclass'),('account_id','subclass'),('run_id','subclass'),
 ('run_id','upper'),('run_id','object'),('session_date','datetime'),('session_date','string')])
def test_exact_submission_context_rejects_numeric_string_and_date_aliases_before_journal(command,field,alias):
 from datetime import datetime, timezone
 context=dict(run_id=RUN,strategy_id=command.binding.identity.strategy_id,
  strategy_revision=command.binding.identity.revision,account_id=command.account_id,session_date=command.binding.session_date)
 value=context[field]
 if alias=='float':value=float(value)
 elif alias=='bool':value=True
 elif alias=='string':value=str(value)
 elif alias=='subclass':value=_StringAlias(value)
 elif alias=='upper':value=value.upper()
 elif alias=='object':value=object()
 elif alias=='datetime':value=datetime.combine(value,datetime.min.time(),timezone.utc)
 context[field]=value
 with pytest.raises(ValueError):command.verify(**context)
 journal=DeclaredNativeJournal(binding=command.binding,run_id=RUN)
 if field in {'strategy_revision','account_id','strategy_id'}:
  with pytest.raises(ValueError):journal.append_declared_native_intent(submission=command,intent=command.intent,
   account_id=context['account_id'],strategy_id=context['strategy_id'],strategy_revision=context['strategy_revision'])
  assert journal.records(RUN)==[]

