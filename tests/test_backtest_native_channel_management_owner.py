from dataclasses import replace
from datetime import datetime,timezone

import pytest

from tests.test_backtest_native_channel_qualification import installed,unit  # noqa: F401
from tests.test_backtest_native_channel_manager_recovery import manager_case
from src.backend.backtest_native_channel_management_owner import NativeChannelManagementOwner
from src.backend.backtest_native_channel_manager_recovery import reprove_native_channel_manager_snapshot


def owner_case(installed):
    rows,policy,contexts=manager_case(installed)
    context=next(iter(contexts.values()))
    elapsed=context['as_of']-datetime(1970,1,1,tzinfo=timezone.utc)
    stamp=(elapsed.days*86400+elapsed.seconds)*1_000_000+elapsed.microseconds
    q=context['quotes'].row(0,named=True)
    quote=dict(ticker=q['ticker'],session_date=q['session_date'],boundary_ms=rows.snapshot['boundary_ms'],
        quote_valid=1,quote_timestamp_us=stamp,bid_int=q['bid_int'],ask_int=q['ask_int'])
    owner=NativeChannelManagementOwner(run_id=rows.snapshot['run_id'],session_date=context['session_date'],policy=policy)
    args=dict(financial=context['current_financial'],original_entry=context['original_entry'],
        group=context['group'],executions=context['executions'],source_entry_intent_id=context['source_entry_intent_id'],
        expected_conid=context['expected_conid'],boundary_ms=rows.snapshot['boundary_ms'])
    return owner,rows,context,quote,args


def test_owner_stages_native_fills_captures_full_root_and_reproves_every_request(installed):
    owner,rows,context,quote,args=owner_case(installed)
    request=owner.stage(context['prepared'],quote,**args)
    assert request is not None
    assert owner.stage(context['prepared'],quote,**args) is request
    pending=owner.pending_requests(boundary_ms=args['boundary_ms'])
    captured,independent=owner.capture(rows.inherited,journal_batch_id=context['batch_id'])
    assert len(captured.pending)==1 and len(captured.sources)==1 and len(captured.executions)==1
    recovered=reprove_native_channel_manager_snapshot(captured,owner.policy,independent)
    assert recovered[0].acquisition==request.acquisition
    assert dict(recovered[0].decision)==dict(request.decision)
    with pytest.raises(ValueError): owner.complete((replace(request),),boundary_ms=args['boundary_ms'])
    assert owner.pending_requests(boundary_ms=args['boundary_ms'])==pending
    owner.complete(pending,boundary_ms=args['boundary_ms'])
    assert owner.pending_requests(boundary_ms=args['boundary_ms'])==()


def test_owner_rejects_changed_native_fill_source_and_foreign_checkpoint(installed):
    owner,rows,context,quote,args=owner_case(installed)
    with pytest.raises(ValueError): owner.stage(context['prepared'],quote,
        **{**args,'source_entry_intent_id':'foreign'})
    owner.stage(context['prepared'],quote,**args)
    with pytest.raises(ValueError): owner.capture(rows.inherited,journal_batch_id='invalid')
    with pytest.raises(ValueError): owner.pending_requests(boundary_ms=args['boundary_ms']+100)
    with pytest.raises(ValueError): owner.stage(context['prepared'],{**quote,'quote_valid':0},**args)
