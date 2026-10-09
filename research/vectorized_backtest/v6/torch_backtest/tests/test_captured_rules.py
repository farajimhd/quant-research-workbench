import numpy as np
import pytest
import torch
from research.vectorized_backtest.v6.torch_backtest.feature_bank import CATALOG
from research.vectorized_backtest.v6.torch_backtest.evolution import sample
from research.vectorized_backtest.v6.torch_backtest.tests.test_sparse_runner import fixture


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA rule capture qualification')
def test_captured_gates_exact_native_with_padding_history_and_missing_features():
    _,inputs,space,_,_=fixture()
    inputs.device=torch.device('cuda')
    rng=np.random.default_rng(2236)
    inputs.tensors['features']=torch.tensor(rng.normal(size=(120,len(CATALOG))),dtype=torch.float32,device='cuda')
    inputs.tensors['feature_valid']=torch.tensor(rng.random((120,len(CATALOG)))>.15,device='cuda')
    population=sample(rng,space,4)
    options=dict(chunk_candles=13,listing_batch=3,workspace_gib=.1)
    native,_=inputs.compile(population,backend='eager',**options)
    captured,_=inputs.compile(population,backend='cudagraph',**options)
    assert torch.equal(native,captured)
