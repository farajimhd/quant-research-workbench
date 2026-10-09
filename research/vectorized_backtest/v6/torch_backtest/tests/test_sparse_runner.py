from types import SimpleNamespace
import numpy as np
import pytest
import json
import torch
from research.vectorized_backtest.v6.torch_backtest.fixtures import synthetic_tape
from research.vectorized_backtest.v6.torch_backtest.evolution import Individual,STAGES,MANAGEMENT_DEFAULT
from research.vectorized_backtest.v6.torch_backtest.genome import StrategySpace,NAMES
from research.vectorized_backtest.v6.torch_backtest.program import Node,Op
from research.vectorized_backtest.v6.torch_backtest.program_runner import ProgramRunner
from research.vectorized_backtest.v6.torch_backtest.sparse_runner import SparseProgramRunner
from research.vectorized_backtest.v6.torch_backtest.sparse_replay import SparseInputs,KEY_STRIDE
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash


def fixture():
    tape=synthetic_tape(seconds=60,listings=2);tape.admission.fill_(1)
    x=SparseInputs.__new__(SparseInputs);x.device=torch.device('cpu')
    x.offsets=np.array([0,60,120])
    top=np.zeros((60,1),dtype=np.int64);top[20:]=1
    x.arrays={'top_indices':top,'feature_keys':np.arange(120)}
    x.tensors={'clocks':tape.clocks,'top_indices':torch.tensor(top),
        'market_keys':torch.cat([tape.clocks+i*KEY_STRIDE for i in range(2)])}
    names={'mark':'close','trade_count':'trades'}
    x.market={name:getattr(tape,names.get(name,name)).T.reshape(-1) for name in
        ('mark','high','low','observed','volume','notional','fill_price','vwap','bid','ask','trade_count')}
    x.market['quote_us']=tape.clocks.repeat(2)*1000000
    x.market['feature_row']=torch.arange(120)
    x.receipt={'identity':{'synthetic':True},'files':{}}
    space=StrategySpace();p=space.default.copy();p[4]=1;p[6]=0
    for name,v in dict(minimum_dollar_volume=0,minimum_trade_count=0,target_step_fraction=.20,terminal_exit_lead_seconds=5).items():p[space.policy_start+NAMES.index(name)]=v
    rules={s:[([Node(Op.CONSTANT,value=int(s=='entry'),unit='bool')],0)] for s in STAGES}
    member=Individual(p.tolist(),rules,{s:[] for s in STAGES},dict(MANAGEMENT_DEFAULT))
    gates=torch.ones((1,120),dtype=torch.uint8)
    return tape,x,space,member,gates


def test_sparse_finance_exact_dense_reference_with_departed_holding():
    tape,x,space,member,gates=fixture()
    dense=torch.ones((60,1,2),dtype=torch.uint8)
    dense[:20,:,1]=0;dense[20:,:,0]=0
    reference=ProgramRunner(tape,space,[member],dense,maximum_fills=512)
    sparse=SparseProgramRunner(x,space,[member],gates,maximum_fills=512)
    before=reference.run();after=sparse.run()
    count=int(reference.fill_count[0]);assert count>0
    torch.testing.assert_close(reference.ledger[0,:count],sparse.ledger[0,:count],rtol=0,atol=0)
    for key,value in before.items():
        if isinstance(value,torch.Tensor):torch.testing.assert_close(value,after[key],rtol=0,atol=0,equal_nan=True)
    assert (sparse.ledger[0,:count,0]>20).any()


def test_missing_structural_sidecar_fails_closed():
    _,x,space,member,gates=fixture();member.policy[6]=1
    with pytest.raises(ValueError,match='structural sidecar'):SparseProgramRunner(x,space,[member],gates)


def test_raw_structural_sidecar_exact_financial_reference_and_seal(tmp_path):
    tape,x,space,member,gates=fixture();member.policy[6]=1
    x.root=tmp_path/'input';x.root.mkdir();(x.root/'complete.json').write_text('{}')
    x.arrays['market_keys']=x.tensors['market_keys'].numpy()
    x.receipt['files']['market_keys.npy']='synthetic-keys'
    side=tmp_path/'structure';side.mkdir()
    targets=tape.level_lower[:,None,:].expand(2,60,15).reshape(120,15).numpy()
    np.save(side/'targets.npy',targets);np.save(side/'valid.npy',np.ones(120,dtype=bool))
    record=dict(status='complete',version='v6-sparse-structural-v1',validation_opened=False,
        input_receipt_sha256=file_hash(x.root/'complete.json'),market_keys_sha256='synthetic-keys',listing_ids=[0,1],
        files={n:file_hash(side/n) for n in ('targets.npy','valid.npy')})
    (side/'complete.json').write_text(json.dumps(record))
    dense=torch.ones((60,1,2),dtype=torch.uint8);dense[:20,:,1]=0;dense[20:,:,0]=0
    reference=ProgramRunner(tape,space,[member],dense,maximum_fills=512)
    sparse=SparseProgramRunner(x,space,[member],gates,structure=side,maximum_fills=512)
    before=reference.run();after=sparse.run();count=int(reference.fill_count[0]);assert count>0
    torch.testing.assert_close(reference.ledger[0,:count],sparse.ledger[0,:count],rtol=0,atol=0)
    for key,value in before.items():
        if isinstance(value,torch.Tensor):torch.testing.assert_close(value,after[key],rtol=0,atol=0,equal_nan=True)
    record['market_keys_sha256']='changed';(side/'complete.json').write_text(json.dumps(record))
    with pytest.raises(ValueError,match='seal mismatch'):SparseProgramRunner(x,space,[member],gates,structure=side)
