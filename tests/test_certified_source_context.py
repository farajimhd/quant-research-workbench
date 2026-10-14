from datetime import date
from unittest.mock import Mock
import pytest
from pipelines.certified_research import context,source
from pipelines.certified_research.contracts import CampaignConfig


def test_source_floor_expands_for_selected_rolling_windows_and_explicit_genesis():
    base=CampaignConfig(date(2026,1,2),date(2026,1,2),short_windows_sessions=(5,))
    normal=context.preparation_start(base,base.start)
    long=CampaignConfig(base.start,base.end,volume_windows_seconds=(60,90*86400),short_windows_sessions=(5,))
    assert context.preparation_start(long,long.start)<normal<base.start
    assert context.preparation_start(base,date(2025,12,1),rebuild_origin=date(2019,1,2))==date(2019,1,2)
    # This does not certify or fabricate a compatible seed.
    assert len(context.plan(base)['blocked'])==2


def test_sparse_inventory_queries_bind_only_requested_dates():
    wanted=(date(2025,6,2),date(2026,1,2));stat=dict(source_date='2025-06-02',updated_at='now')
    query=Mock(side_effect=[[stat],[dict(day='2025-06-02',statuses=['ok'])]])
    inventory=source.inventory(query,wanted[0],wanted[-1],sessions=wanted)
    assert inventory['missing']==(wanted[-1],) and set(inventory['source_hashes'])=={'2025-06-02'}
    assert all(' IN (' in call.args[0] for call in query.call_args_list)
    query=Mock(return_value=[dict(source_date='2025-06-03')])
    with pytest.raises(ValueError,match='unrequested'):
        source.inventory(query,wanted[0],wanted[-1],sessions=wanted)


def test_invalid_explicit_sessions_fail_before_inventory_queries():
    query=Mock()
    with pytest.raises(ValueError):source.inventory(query,date(2025,1,1),date(2026,1,2),sessions=(date(2026,1,3),))
    query.assert_not_called()
