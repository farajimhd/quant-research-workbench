from dataclasses import replace
from types import SimpleNamespace
import pytest
from src.backend.backtest_typed_publisher import TypedBacktestReceipt
from src.trading_runtime.strategy_one_management_snapshot import ManagerSnapshotHead
from src.trading_runtime.strategy_profit_giveback_arm import profit_arm_candidate
from src.trading_runtime.strategy_profit_giveback_arm_reference import confirm_profit_arm_reference
from test_strategy_profit_giveback_source import fixture


@pytest.mark.parametrize('corruption',[None,'receipt_sequence','receipt_batch','head_changed','root_hash','boundary','high'])
def test_reference_requires_native_attested_reader_and_matching_receipt(monkeypatch,corruption):
    """Mocked reader routing test, not a database attestation claim."""
    from src.trading_runtime import strategy_one_management_snapshot as snapshots
    _,state,held=fixture()
    candidate=profit_arm_candidate(state,held,already_checkpointed=False)
    head=ManagerSnapshotHead('run',7,'batch','a'*64,1)
    receipt=TypedBacktestReceipt(7,'batch','cursor')
    if corruption=='receipt_sequence':receipt=replace(receipt,last_sequence=8)
    if corruption=='receipt_batch':receipt=replace(receipt,last_batch_id='other')
    root={'snapshot_id':'14ff5fc6-1e02-4444-b2af-dd567da70f3c','content_hash':'a'*64,'boundary_ms':9900}
    if corruption=='root_hash':root['content_hash']='b'*64
    if corruption=='boundary':root['boundary_ms']=9800
    if corruption=='high':
        key=state.position_highs[0][0];state=replace(state,position_highs=((key,111000),))
    calls=[]
    class Keeper:
        def read_head(self,**kwargs):
            calls.append('head')
            return replace(head,keeper_version=2) if corruption=='head_changed' and len(calls)>2 else head
    def read(client,keeper,**kwargs):
        assert kwargs['checkpoint_sequence']==7 and kwargs['run_id']=='run'
        calls.append('attested');return state
    monkeypatch.setattr(snapshots,'load_attested_manager_snapshot',read)
    monkeypatch.setattr(snapshots,'load_unattested_manager_snapshot_rows',lambda *args,**kwargs:SimpleNamespace(snapshot=root))
    if corruption:
        with pytest.raises(ValueError):confirm_profit_arm_reference(object(),Keeper(),candidate,held,receipt,run_id='run')
    else:
        reference=confirm_profit_arm_reference(object(),Keeper(),candidate,held,receipt,run_id='run')
        assert reference.candidate==candidate and reference.checkpoint_sequence==7
        assert reference.snapshot_id==root['snapshot_id'] and 'attested' in calls
