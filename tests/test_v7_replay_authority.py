"""Source initialization must not masquerade as a historical data revision."""
from copy import deepcopy
import pytest
from src.backend.replay_run_service import ReplayRunController


def controller():
    run=object.__new__(ReplayRunController)
    run._data_authority={};run._journal=None
    return run


def snapshot(loaded=False):
    return dict(session_date='2026-08-21',provenance=dict(checkpoint_hash='seed',catalog_hash='catalog'),
        bars_processed=int(loaded),source_audit=dict(source_revision=dict(token='tape',request_complete=True)) if loaded else {})


@pytest.mark.parametrize('order',[(False,True,False,True),(True,False,True)])
def test_seed_and_source_are_pinned_across_independent_lane_initialization(order):
    run=controller()
    for loaded in order:run._record_v7_data_authority('SUGP',snapshot(loaded))
    assert set(run._data_authority)=={'v7:SUGP:2026-08-21','v7:SUGP:2026-08-21:source'}
    assert run._data_authority['v7:SUGP:2026-08-21:source']['source_revision']['token']=='tape'


@pytest.mark.parametrize('field',['checkpoint','source'])
def test_real_checkpoint_or_source_revision_change_still_stops_run(field):
    run=controller();original=snapshot(True)
    run._record_v7_data_authority('SUGP',original)
    changed=deepcopy(original)
    if field=='checkpoint':changed['provenance']['checkpoint_hash']='changed'
    else:changed['source_audit']['source_revision']['token']='changed'
    with pytest.raises(RuntimeError,match='Historical data authority changed'):
        run._record_v7_data_authority('SUGP',changed)


def test_consumed_bars_cannot_have_missing_or_incomplete_source_authority():
    run=controller();missing=snapshot();missing['bars_processed']=1
    with pytest.raises(RuntimeError,match='without source revision'):run._record_v7_data_authority('SUGP',missing)
    incomplete=snapshot(True);incomplete['source_audit']['source_revision']['request_complete']=False
    with pytest.raises(RuntimeError,match='incomplete'):run._record_v7_data_authority('SUGP',incomplete)
