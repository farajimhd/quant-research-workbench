import json
import torch
import numpy as np
from research.vectorized_backtest.v6.torch_backtest import profile_sparse
from research.vectorized_backtest.v6.torch_backtest.profile_cpu_comparison import numeric_parity
from research.vectorized_backtest.v6.torch_backtest.tests.test_sparse_runner import fixture
from research.vectorized_backtest.v6.torch_backtest.evolution import sample


def test_cpu_profiler_real_financial_replay_without_cuda(tmp_path,monkeypatch):
    tape,inputs,space,member,gates=fixture()
    inputs.root=tmp_path/'inputs'/'synthetic';inputs.root.mkdir(parents=True)
    (inputs.root/'complete.json').write_text('{}')
    inputs.receipt['identity']['session']={'day':'synthetic'}
    inputs.arrays['clocks']=tape.clocks.numpy();inputs.bytes=1
    inputs.compile=lambda members,**kwargs:(gates,0.)
    monkeypatch.setattr(profile_sparse,'SparseInputs',lambda *args,**kwargs:inputs)
    monkeypatch.setattr(profile_sparse,'sample',lambda *args,**kwargs:[member])
    monkeypatch.setattr(profile_sparse,'configure_caches',lambda *args:None)
    monkeypatch.setattr(torch,'set_num_interop_threads',lambda *args:None)
    def mkdir(path):path.mkdir(parents=True,exist_ok=True);return path
    monkeypatch.setattr(profile_sparse,'require_runtime',mkdir)
    def forbidden(*args,**kwargs):raise AssertionError('CPU profiler touched CUDA')
    for name in ('synchronize','reset_peak_memory_stats','max_memory_allocated','is_available'):
        monkeypatch.setattr(torch.cuda,name,forbidden)
    output=tmp_path/'cpu';previous=torch.get_num_threads()
    try:
        profile_sparse.main(['--inputs',str(inputs.root.parent),'--output',str(output),'--day','synthetic',
            '--device','cpu','--backend','eager','--batch-size','1','--seconds','60','--repeats','2'])
    finally:torch.set_num_threads(previous)
    receipt=json.loads((output/'receipt.json').read_text())
    assert receipt['device']=='cpu' and receipt['full_session'] and receipt['repeated_fill_receipts_exact']
    assert receipt['measurements'][0]['fills']>0
    assert json.loads((output/'financial-audit.json').read_text())['status']=='passed'


def test_device_parity_preserves_null_patterns_integer_counts_and_nested_episodes():
    assert numeric_parity([[None,1.],[2.]],[[None,1.+1e-10],[2.]])
    assert not numeric_parity([None,1.],[0.,1.])
    assert not numeric_parity([2],[3])
    assert not numeric_parity([[1.,2.]],[[1.]])


def test_exact_sealed_population_restores_without_resampling(tmp_path):
    _,_,space,_,_=fixture()
    members=sample(np.random.default_rng(2236),space,4)
    path=tmp_path/'population.json';path.write_text(json.dumps([m.payload() for m in members]))
    assert [m.payload() for m in profile_sparse.load_profile_population(path)]==[m.payload() for m in members]
