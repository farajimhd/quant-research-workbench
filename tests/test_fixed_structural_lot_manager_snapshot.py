"""Pure serializer seam controls; no installed writer authority."""
from dataclasses import replace,asdict
from datetime import date
import pytest
from tests.test_strategy_one_management_snapshot import _rows
from src.trading_runtime.strategy_one_management_snapshot import (
    restore_manager_snapshot,_project_manager_snapshot_scalar,
)
from src.trading_runtime.strategy_one_protection_snapshot import _serialize_protection_snapshot,_digest


def fixture():
    old=_rows();state=restore_manager_snapshot(old)
    args=dict(run_id=old.snapshot['run_id'],session_date=date.fromisoformat(old.snapshot['session_date']),
        checkpoint_sequence=old.snapshot['checkpoint_sequence'],state=state)
    return old,state,args


def test_default_scalar_rows_equal_prevalidated_seam():
    old,state,args=fixture()
    assert asdict(_project_manager_snapshot_scalar(**args))==asdict(
        _project_manager_snapshot_scalar(**args,_protection_rows=old.protection))


@pytest.mark.parametrize('field,value',[('run_id','foreign'),('session_date','2026-08-19'),
    ('checkpoint_sequence',43),('boundary_ms',31100),('position_count',0)])
def test_resealed_wrong_root_cannot_enter_seam(field,value):
    old,state,args=fixture()
    root={**old.protection.snapshot,field:value};root['content_hash']=_digest({k:v for k,v in root.items() if k!='content_hash'})
    with pytest.raises(ValueError,match='complete capture'):
        _project_manager_snapshot_scalar(**args,_protection_rows=replace(old.protection,snapshot=root))


@pytest.mark.parametrize('change',['missing','duplicate','target','stop','resistance'])
def test_precomputed_state_or_roster_cannot_substitute_capture(change):
    old,state,args=fixture();rows=old.protection
    if change=='missing':rows=replace(rows,states=())
    elif change=='duplicate':rows=replace(rows,states=rows.states*2)
    elif change=='resistance':rows=replace(rows,resistances=({'foreign':'geometry'},))
    else:rows=replace(rows,states=({**rows.states[0],change:'10.020000000000000000'},))
    with pytest.raises(ValueError):_project_manager_snapshot_scalar(**args,_protection_rows=rows)


def test_pure_selected_serializer_preserves_original_target_while_stop_passes_it():
    old,state,args=fixture();key,position=state.positions[0]
    state=replace(state,positions=((key,replace(position,stop=10.32)),))
    positions={(k[0],k[2],k[1]):v for k,v in state.positions}
    rows=_serialize_protection_snapshot(run_id=args['run_id'],session_date=args['session_date'],
        checkpoint_sequence=args['checkpoint_sequence'],boundary_ms=state.boundary_ms,positions=positions)
    projected=_project_manager_snapshot_scalar(**{**args,'state':state},_protection_rows=rows)
    assert projected.protection.states[0]['target']==rows.states[0]['target']
    assert float(projected.protection.states[0]['target'])==10.3
    assert float(projected.protection.states[0]['stop'])==10.32
    # This pure row relation is not a capability. The ordinary default route
    # still rejects it; installed selected issuance must independently prove
    # actual residual/acquiring lot targets above the proposed stop.
    with pytest.raises(ValueError):_project_manager_snapshot_scalar(**{**args,'state':state})
