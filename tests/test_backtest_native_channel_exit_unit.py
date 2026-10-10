"""Complete scalar native transport; no installed writer/OMS authority implied."""
from dataclasses import replace
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_exit_recovery import recovery_case
from src.backend.backtest_native_channel_exit_recovery import reprove_native_channel_exit_rows
from src.backend.backtest_native_channel_exit_unit import (
    project_native_channel_exit_unit, native_channel_exit_intent, reprove_native_channel_exit_unit,
)
from src.trading_runtime.native_channel_management_policy import NativeChannelManagementPolicy
from src.trading_runtime.numbered_fixed_strategy import session_policy_payload
from src.trading_runtime.arte_journal_commit_v4 import prepare_commit_v4
from src.trading_runtime.arte_journal_writer import typed_contract_row
from src.trading_runtime.arte_native_channel_exit_schema import DECISION


def unit_case(installed, monkeypatch):
    rows, args = recovery_case(installed)
    request = reprove_native_channel_exit_rows(*rows, **args)
    policy = NativeChannelManagementPolicy(args['policy'],10,100)
    selected = SimpleNamespace(native_channel_management_policy=policy,
        policy_json=json.dumps({'session_policy':session_policy_payload()}))
    monkeypatch.setattr('src.trading_runtime.numbered_fixed_strategy.numbered_fixed_strategy',lambda _n:selected)
    context = {k:v for k,v in args.items() if k not in
        ('prepared','policy','quotes','session_date','group','executions','source_entry_intent_id','current_financial')}
    context.update(management_policy=policy,recorded_at=args['as_of'],
        attempt_id='99999999-9999-4999-8999-999999999999',
        prior_batch_id='88888888-8888-4888-8888-888888888888',sequence=13,source_cursor='cursor')
    return request,args['group'],args['executions'],context,selected


def test_complete_native_unit_seals_parent_and_every_source_fill_family(installed,monkeypatch):
    request,group,fills,args,_ = unit_case(installed,monkeypatch)
    packet = project_native_channel_exit_unit(request,group,fills,**args)
    assert packet.base.intents[0]['quantity']=='5.000000000000000000'
    assert packet.base.intents[0]['action']=='exit'
    families = packet.sealed_families()
    commit, inventory = prepare_commit_v4(run_id=packet.base.run_id,run_month=packet.base.run_month,
        attempt_id=packet.base.attempt_id,batch_id=packet.base.batch_id,prior_batch_id=packet.base.prior_batch_id,
        first_sequence=13,last_sequence=13,source_cursor='cursor',status='running',
        sealed_families=families,committed_at=args['recorded_at'])
    assert commit and {'trading_native_channel_exit_v1','trading_native_channel_exit_source_v1',
        'trading_native_channel_exit_execution_v1'} <= {row['family_name'] for row in inventory}
    assert project_native_channel_exit_unit(request,group,fills,**args).base.intents == packet.base.intents
    with pytest.raises(TypeError): packet.decision['ticker']='foreign'


@pytest.mark.parametrize('defect',['missing','duplicate','quantity','price','clock','parent','future_checkpoint'])
def test_complete_unit_rejects_tampered_or_incomplete_graph(installed,monkeypatch,defect):
    request,group,fills,args,_ = unit_case(installed,monkeypatch)
    packet = project_native_channel_exit_unit(request,group,fills,**args)
    changes={}
    if defect=='missing': changes['sources']=()
    elif defect=='duplicate': changes['executions']=packet.executions*2
    elif defect in ('quantity','price'):
        field='quantity' if defect=='quantity' else 'reference_price'
        changes['base']=replace(packet.base,intents=({**packet.base.intents[0],field:'1'},))
    elif defect=='clock':
        at=request.acquisition.first_execution_time+timedelta(seconds=1)
        changes['base']=replace(packet.base,events=({**packet.base.events[0],'event_time':at.isoformat()},))
    else:
        header={k:v for k,v in packet.decision.items() if k!='content_hash'}
        if defect=='future_checkpoint': header['source_manager_checkpoint_sequence']=packet.base.first_sequence
        else: header['parent_record_id']='77777777-7777-4777-8777-777777777777'
        changes['decision']=typed_contract_row(DECISION,header)
    with pytest.raises(ValueError): replace(packet,**changes)


@pytest.mark.parametrize('defect',['undeclared','rth','foreign_owner'])
def test_intent_factory_requires_registered_policy_extended_session_and_owner(installed,monkeypatch,defect):
    request,_,_,args,selected=unit_case(installed,monkeypatch)
    if defect=='undeclared': selected.native_channel_management_policy=None
    if defect=='rth': request=replace(request,decision={**request.decision,'decision_day_ms':43_200_000})
    if defect=='foreign_owner': request=replace(request,financial=replace(request.financial,account_id='foreign'))
    with pytest.raises(ValueError): native_channel_exit_intent(request,
        strategy_number=args['original_entry'].strategy_number,management_policy=args['management_policy'])


@pytest.mark.parametrize('defect',['bound','policy_type'])
def test_publication_cannot_override_complete_family_bound(installed,monkeypatch,defect):
    request,group,fills,args,_=unit_case(installed,monkeypatch)
    if defect=='bound': args['max_rows_per_family'] += 1
    else: args['management_policy']=None
    with pytest.raises(ValueError): project_native_channel_exit_unit(request,group,fills,**args)


@pytest.mark.parametrize('defect',[None,'metadata','execution_policy','cursor','sequence','quote'])
def test_complete_unit_reproof_uses_independent_source_and_journal_envelope(installed,monkeypatch,defect):
    request,group,fills,args,_=unit_case(installed,monkeypatch)
    packet=project_native_channel_exit_unit(request,group,fills,**args)
    _,recovery=recovery_case(installed)
    envelope={key:args[key] for key in ('management_policy','recorded_at','attempt_id',
        'prior_batch_id','sequence','source_cursor')}
    if defect in ('metadata','execution_policy'):
        intent=dict(packet.base.intents[0])
        if defect=='metadata': intent['metadata_json']='{"unapproved":true}'
        else: intent['execution_policy_name']='adaptive_normal'
        packet=replace(packet,base=replace(packet.base,intents=(intent,)))
    elif defect=='cursor': envelope['source_cursor']='foreign-cursor'
    elif defect=='sequence': envelope['sequence']+=1
    elif defect=='quote':
        import polars as pl
        recovery['quotes']=recovery['quotes'].with_columns((pl.col('bid_int')+1).alias('bid_int'))
    if defect is None:
        verified=reprove_native_channel_exit_unit(packet,**envelope,**recovery)
        assert verified.base==packet.base
        assert verified.sealed_families()==packet.sealed_families()
    else:
        with pytest.raises(ValueError): reprove_native_channel_exit_unit(packet,**envelope,**recovery)
