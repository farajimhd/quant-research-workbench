from copy import deepcopy
import json
import numpy as np
import pytest
import torch
from research.vectorized_backtest.v6.torch_backtest.tests.test_sparse_runner import fixture
from research.vectorized_backtest.v6.torch_backtest.program_runner import ProgramRunner
from research.vectorized_backtest.v6.torch_backtest.compact_runner import CompactProgramRunner
from research.vectorized_backtest.v6.torch_backtest.genome import NAMES
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash


def compare(*,cycling=False,population=1,compiled=False,structure=None,capacity=2):
    tape,x,space,member,gates=fixture();x.offsets=np.array([0,60,120])
    x.arrays['top_indices']=x.arrays['top_indices'].astype(np.int32)
    x.tensors['top_indices']=torch.tensor(x.arrays['top_indices'])
    members=[deepcopy(member) for _ in range(population)]
    if structure is not None:
        x.root=structure/'input';x.root.mkdir();(x.root/'complete.json').write_text('{}')
        x.arrays['market_keys']=x.tensors['market_keys'].numpy()
        x.receipt['files']['market_keys.npy']='synthetic-keys'
        targets=tape.level_lower[:,None,:].expand(2,60,15).reshape(120,15).numpy()
        np.save(structure/'targets.npy',targets);np.save(structure/'valid.npy',np.ones(120,dtype=bool))
        (structure/'complete.json').write_text(json.dumps(dict(status='complete',version='v6-sparse-structural-v1',validation_opened=False,
            input_receipt_sha256=file_hash(x.root/'complete.json'),market_keys_sha256='synthetic-keys',listing_ids=[0,1],
            files={n:file_hash(structure/n) for n in ('targets.npy','valid.npy')})))
        for v in members:v.policy[6]=1
    gates=gates.expand(population,-1).clone()
    if cycling:
        x.arrays['top_indices'][40:]=0;x.tensors['top_indices']=torch.tensor(x.arrays['top_indices'])
        for v in members:
            v.policy[space.policy_start+NAMES.index('target_step_fraction')]=.005
            v.policy[space.policy_start+NAMES.index('maximum_entry_drift_fraction')]=.001
    dense=torch.ones((60,population,2),dtype=torch.uint8)
    for clock,top in enumerate(x.arrays['top_indices']):
        dense[clock,:,np.arange(2)!=top[0]]=0
    # Different candidates can hold different identities at a shared clock.
    if population>1:
        gates[1,:20]=0;dense[:20,1]=0
    reference=ProgramRunner(tape,space,members,dense,maximum_fills=512)
    compact=CompactProgramRunner(x,space,members,gates,holding_capacity=capacity,structure=structure,maximum_fills=512)
    if compiled:compact.step=torch.compile(compact.tick,backend='eager',fullgraph=True)
    before=reference.run();after=compact.run()
    assert int(reference.fill_count.sum())>0
    for lane in range(population):
        count=int(reference.fill_count[lane])
        torch.testing.assert_close(reference.ledger[lane,:count],compact.ledger[lane,:count],rtol=0,atol=0)
    for name,value in before.items():
        if isinstance(value,torch.Tensor):torch.testing.assert_close(value,after[name],rtol=0,atol=0,equal_nan=True,msg=name)
    return compact


def test_compact_departed_holding_exact_financial_reference():compare()


def test_compact_candidate_identities_and_archived_order_counters():
    runner=compare(cycling=True,population=2)
    assert int(runner.archived_requested_entry_shares.sum())>0


def test_compact_fullgraph_candidate_state_matches_reference():compare(population=2,compiled=True)


def test_compact_structural_targets_match_financial_reference(tmp_path):
    runner=compare(population=2,structure=tmp_path)
    _,_,space,member,_=fixture();member.policy[6]=1
    second=CompactProgramRunner(runner.inputs,space,[member,deepcopy(member)],runner.sparse_gates.clone(),
        holding_capacity=2,structure=tmp_path,maximum_fills=512)
    assert second.structural is runner.structural
    assert second.tape.provenance['structural_receipt_sha256']==runner.tape.provenance['structural_receipt_sha256']
    with pytest.raises(ValueError,match='authority changed'):
        CompactProgramRunner(runner.inputs,space,[member,deepcopy(member)],runner.sparse_gates.clone(),
            holding_capacity=2,structure=tmp_path/'different',maximum_fills=512)


def test_compact_capacity_exhaustion_rejects_result():
    with pytest.raises(RuntimeError,match='overflow|capacity|exhaust',):compare(capacity=1)


def test_sparse_population_reuse_changes_parameters_and_gates_exactly():
    _,inputs,space,member,gates=fixture()
    member.policy[space.policy_start+NAMES.index('adaptive_window')]=31
    reused=CompactProgramRunner(inputs,space,[member],gates.clone(),holding_capacity=2,maximum_fills=512)
    reused.run()
    changed=deepcopy(member)
    changed.policy[space.policy_start+NAMES.index('adaptive_window')]=32
    changed.policy[space.policy_start+NAMES.index('target_step_fraction')]=.01
    changed_gates=gates.clone();changed_gates[:,:10]=0
    pointer=reused.sparse_gates.data_ptr()
    reused.set_sparse_population([changed],changed_gates)
    fresh=CompactProgramRunner(inputs,space,[changed],changed_gates,holding_capacity=2,maximum_fills=512)
    before=fresh.run();after=reused.run()
    assert pointer==reused.sparse_gates.data_ptr()
    for name,value in before.items():
        if isinstance(value,torch.Tensor):torch.testing.assert_close(value,after[name],rtol=0,atol=0,equal_nan=True,msg=name)
    count=int(fresh.fill_count[0]);torch.testing.assert_close(fresh.ledger[0,:count],reused.ledger[0,:count],rtol=0,atol=0)


def test_shared_source_rows_exact_for_repeated_held_and_empty_slots():
    _,inputs,_,_,_=fixture()
    union=torch.tensor([0,1]);ids=torch.tensor([[1,0,-1],[0,1,1]])
    indices=torch.searchsorted(union,ids.clamp_min(0))
    for clock in inputs.tensors['clocks']:
        source=inputs.lookup(union,clock)
        reference=inputs.lookup(ids,clock)
        shared=inputs.lookup(ids,clock,source_rows=source['source_row'][indices])
        for name,value in reference.items():
            torch.testing.assert_close(value,shared[name],rtol=0,atol=0,equal_nan=True,msg=name)
