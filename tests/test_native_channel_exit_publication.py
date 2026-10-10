"""Real native reproof/INSERT encoding; checkpoint transport and dispatch controlled."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_confirmed_exit_unit import confirmed_unit_case
from src.backend.backtest_native_channel_exit_unit import prepare_confirmed_native_channel_exit_unit
from src.trading_runtime.native_channel_exit_journal_context import NativeChannelExitJournalContext
from src.trading_runtime.native_channel_exit_publication import (
    issue_native_channel_exit_publication,require_native_channel_exit_publication,
    refresh_native_channel_exit_publication,
)
from src.trading_runtime.arte_journal_writer import _insert,_CONTRACTS
from src.trading_runtime.arte_native_channel_exit_schema import TABLES


def publication_case(installed,monkeypatch):
    from src.trading_runtime.strategy_registry import initialize_numbered_fixed_strategies
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    initialize_numbered_fixed_strategies()
    parent=numbered_fixed_strategy(115)
    scope=confirmed_unit_case(installed,monkeypatch)
    packet=prepare_confirmed_native_channel_exit_unit(**scope)
    context=NativeChannelExitJournalContext(packet,scope['management_policy'],scope['contexts'],
        scope['first_price_source'],**{key:scope[key] for key in (
        'recorded_at','attempt_id','batch_id','parent_record_id','decision_record_id')})
    writer=SimpleNamespace(native_channel_management_policy=scope['management_policy'],
        confirmed_original_risk_policy=parent.confirmed_original_risk_policy,
        entry_spread_risk_profile=True,typed_insert_strict=True)
    return writer,scope,context


def issue(writer,scope,context):
    return issue_native_channel_exit_publication(writer,scope['client'],scope['manager_keeper'],
        scope['broker_keeper'],scope['receipt'],context,pending_record_id=scope['pending_record_id'])


def test_image_reproves_current_native_unit_and_freezes_complete_families(installed,monkeypatch):
    writer,scope,context=publication_case(installed,monkeypatch)
    image=issue(writer,scope,context)
    confirmed,families=require_native_channel_exit_publication(image,client=writer)
    assert confirmed is context and len(families)==5
    assert image.sequence==scope['receipt'].last_sequence+1
    assert refresh_native_channel_exit_publication(image,client=writer) is context
    assert all(table.name not in _CONTRACTS for table in TABLES)


@pytest.mark.parametrize('defect',['copy','client','policy','rows','sequence','writer_profile','read_policy'])
def test_public_image_fields_do_not_grant_authority(installed,monkeypatch,defect):
    writer,scope,context=publication_case(installed,monkeypatch)
    image=issue(writer,scope,context)
    if defect=='copy': image=replace(image)
    elif defect=='client': writer=SimpleNamespace(**vars(writer))
    elif defect=='policy': writer.native_channel_management_policy=None
    elif defect=='rows': object.__setattr__(image,'rows_json','{}')
    elif defect=='sequence': object.__setattr__(image,'sequence',image.sequence+1)
    elif defect=='writer_profile': writer.confirmed_original_risk_policy=None
    elif defect=='read_policy': scope['client'].native_channel_management_policy=None
    with pytest.raises(ValueError): require_native_channel_exit_publication(image,client=writer)


@pytest.mark.parametrize('defect',['head','executions'])
def test_refresh_rejects_changed_native_heads_or_original_fills(installed,monkeypatch,defect):
    writer,scope,context=publication_case(installed,monkeypatch)
    image=issue(writer,scope,context)
    if defect=='head':
        keeper=scope['manager_keeper']
        keeper.head=replace(keeper.head,checkpoint_sequence=keeper.head.checkpoint_sequence+1)
    else:
        monkeypatch.setattr('src.backend.backtest_v4_execution_restore.load_v4_broker_executions',
            lambda *_a,**_k:[])
    with pytest.raises(ValueError): refresh_native_channel_exit_publication(image,client=writer)


def test_real_encoder_accepts_only_complete_owner_issued_exit_families(installed,monkeypatch):
    writer,scope,context=publication_case(installed,monkeypatch)
    image=issue(writer,scope,context)
    _,families=require_native_channel_exit_publication(image,client=writer)
    calls=[]
    class Dispatch:
        def execute_typed_insert(self,_client,**kwargs): calls.append(kwargs)
    writer.typed_insert_dispatch=Dispatch()
    kwargs=dict(journal_profile='backtest_v4',dispatch_sequence=image.sequence,
        dispatch_batch_id=image.batch_id,dispatch_native_channel_exit_image=image)
    for contract in TABLES:
        rows=tuple(families[contract.name])
        _insert(writer,contract.name,rows,f'{image.batch_id}:{contract.name}:v4',**kwargs)
    assert len(calls)==3 and {call['table'] for call in calls}=={table.name for table in TABLES}
    table=TABLES[1].name
    token=f'{image.batch_id}:{table}:v4'
    for rows in ((),tuple(families[table])*2,({**families[table][0],'close_int':0},)):
        with pytest.raises(ValueError): _insert(writer,table,rows,token,**kwargs)
    for changes in (dict(journal_profile='v1'),dict(dispatch_sequence=image.sequence+1),
            dict(dispatch_manager_snapshot_hash='f'*64),dict(dispatch_sync_revision=0)):
        with pytest.raises(ValueError):
            _insert(writer,table,tuple(families[table]),token,**{**kwargs,**changes})
    with pytest.raises(ValueError): _insert(writer,table,tuple(families[table]),'foreign',**kwargs)
    assert len(calls)==3
