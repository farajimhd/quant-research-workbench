import pytest
import torch
from research.vectorized_backtest.v6.torch_backtest.run_search import seal_ledger


def test_interrupted_receipt_recovery_reconciles_financial_fills(tmp_path):
    path=tmp_path/'fills.pt';ledger=torch.arange(36,dtype=torch.float64).reshape(2,2,9)
    counts=torch.tensor([2,1]);original=seal_ledger(path,ledger,counts)
    padding=ledger.clone();padding[1,1]=0
    assert seal_ledger(path,padding,counts)==original
    altered=ledger.clone();altered[0,0,5]+=1
    with pytest.raises(ValueError,match='fill mismatch'):
        seal_ledger(path,altered,counts)
    with pytest.raises(ValueError,match='identity/count mismatch'):
        seal_ledger(path,ledger,torch.tensor([1,1]))
    assert seal_ledger(path,ledger,counts)==original
