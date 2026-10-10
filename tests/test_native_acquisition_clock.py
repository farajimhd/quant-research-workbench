from dataclasses import replace
from datetime import date, datetime, timezone, timedelta

import pytest

from src.trading_runtime.ibkr_schema import Execution
from src.trading_runtime.order_management import OrderGroupSnapshot, OrderManagementState
from src.trading_runtime.native_acquisition_clock import acquisition_clock_from_entry_executions


FIRST = datetime(2026, 8, 4, 12, 59, 59, 999999, tzinfo=timezone.utc)
NOW = FIRST + timedelta(minutes=3)


def group():
    return OrderGroupSnapshot(group_id='group', intent_id='intent', account_id='account',
        ticker='TEST', action='enter_long', state=next(iter(OrderManagementState)),
        client_order_ids=('entry-client',), broker_order_ids=('entry', 'stop'),
        submitted_at=FIRST-timedelta(seconds=20), updated_at=NOW, filled_quantity=5.0,
        remaining_quantity=0.0, warning_message_ids=(), rejection_reason='',
        decision_to_submit_ms=None, policy_version=1, reentry_after_fill=False,
        assignment_id='assignment')


def fill(name, size, at=FIRST, **changes):
    return replace(Execution(name, 'TEST', 'B', 'entry-client', at,
        int(at.timestamp()*1000), size, 2.0, 'entry', 'account', 11), **changes)


def clock(g, fills, **changes):
    return acquisition_clock_from_entry_executions(g, fills, **{
        'session_date':date(2026, 8, 4), 'as_of':NOW,
        'source_entry_intent_id':'intent', 'expected_conid':11, **changes})


def test_partial_entry_fills_keep_exact_first_microsecond_not_submission_or_last_fill():
    a=fill('first',2);b=fill('second',3,FIRST+timedelta(seconds=5))
    protection=fill('sell',1,FIRST+timedelta(seconds=10),side='S',order_id='stop')
    unrelated=fill('other',100,account='other')
    later_add=fill('add',9,FIRST+timedelta(seconds=50),order_id='add')
    result=clock(group(),(later_add,b,protection,unrelated,a))
    assert result.acquisition_day_us==32_399_999_999
    assert result.first_execution_id=='first'
    assert result.execution_ids==('first','second')
    assert result.source_entry_intent_id=='intent' and result.assignment_id=='assignment'


@pytest.mark.parametrize('defect',['future','naive','foreign_day','ticker','conid','side',
                                 'quantity','duplicate','missing','add_group','intent'])
def test_unverified_or_foreign_acquisition_evidence_fails_closed(defect):
    g=group();a=fill('first',2);b=fill('second',3,FIRST+timedelta(seconds=5));values=(a,b);options={}
    if defect=='future': values=(a,replace(b,trade_time=NOW+timedelta(microseconds=1)))
    if defect=='naive': values=(replace(a,trade_time=FIRST.replace(tzinfo=None)),b)
    if defect=='foreign_day': values=(replace(a,trade_time=FIRST-timedelta(days=1)),b)
    if defect=='ticker': values=(replace(a,symbol='OTHER'),b)
    if defect=='conid': values=(replace(a,conid=12),b)
    if defect=='side': values=(replace(a,side='BUY'),b)
    if defect=='quantity': values=(replace(a,size=1),b)
    if defect=='duplicate': values=(a,b,a)
    if defect=='missing': values=()
    if defect=='add_group': g=replace(g,action='add_long')
    if defect=='intent': options['source_entry_intent_id']='other-intent'
    with pytest.raises(ValueError): clock(g,values,**options)


def test_reentry_uses_new_group_and_never_borrows_prior_acquisition():
    g=replace(group(),intent_id='reentry',broker_order_ids=('reentry-order',),filled_quantity=1)
    current=fill('new',1,FIRST+timedelta(seconds=30),order_id='reentry-order')
    result=clock(g,(fill('old',5),current),source_entry_intent_id='reentry')
    assert result.first_execution_id=='new'
    assert result.acquisition_day_us==32_429_999_999
