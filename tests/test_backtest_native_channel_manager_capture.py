import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_manager_recovery import manager_case
from src.backend.backtest_native_channel_manager_capture import capture_native_channel_manager_rows
from src.backend.backtest_native_channel_manager_recovery import reprove_native_channel_manager_snapshot


def test_complete_capture_reproduces_existing_normalized_snapshot(installed):
    rows,policy,contexts=manager_case(installed)
    requests=dict(zip(sorted(contexts),reprove_native_channel_manager_snapshot(rows,policy,contexts),strict=True))
    captured=capture_native_channel_manager_rows(rows.inherited,requests,contexts,policy=policy)
    assert captured==rows
    empty=capture_native_channel_manager_rows(rows.inherited,{}, {},policy=policy)
    assert empty.pending==empty.sources==empty.executions==()


@pytest.mark.parametrize('defect',['missing','decision','policy','source'])
def test_capture_rejects_incomplete_or_changed_source(installed,defect):
    rows,policy,contexts=manager_case(installed)
    requests=dict(zip(sorted(contexts),reprove_native_channel_manager_snapshot(rows,policy,contexts),strict=True))
    key=next(iter(contexts))
    if defect=='missing': contexts={}
    elif defect=='decision': contexts[key]['decision_record_id']='foreign'
    elif defect=='policy': contexts[key]['policy']=None
    else:
        from uuid import uuid5,NAMESPACE_URL
        contexts[key]['source_entry_intent_id']=str(uuid5(NAMESPACE_URL,
            'different-entry:'+contexts[key]['source_entry_intent_id']))
    with pytest.raises(ValueError):
        capture_native_channel_manager_rows(rows.inherited,requests,contexts,policy=policy)
