import pytest
import torch
from research.vectorized_backtest.v6.torch_backtest.financial_audit import audit_fills


def test_partial_fills_reconcile_cash_fees_and_actual_elapsed_hold(tmp_path):
    path=tmp_path/'fills.pt'
    rows=[[10,0,0,1,5,10,1,0,1],[12,0,0,1,5,11,1,0,2],
          [20,0,0,-1,7,12,1,1,3],[22,0,0,-1,3,13,1,1,4]]
    torch.save(dict(ledger=torch.tensor([rows],dtype=torch.float64),counts=torch.tensor([4])),path)
    metrics={name:[value] for name,value in dict(fill_count=4,cash=10014,fees=4,open_quantity=0,
        open_positions=0,sold_shares=10,sold_share_seconds=106,positions_opened=1,net_pnl=14,terminal_valid=True).items()}
    report=audit_fills(path,metrics)[0]
    assert report['position_win_rate']==1 and report['closed_positions']==1
    metrics['closed_position_duration_samples']=[[12]]
    audit_fills(path,metrics)
    metrics['closed_position_duration_samples']=[[2]]
    with pytest.raises(ValueError,match='elapsed duration'):
        audit_fills(path,metrics)
    metrics['closed_position_duration_samples']=[[12]]
    metrics['sold_share_seconds']=[12]  # Counting observations is not elapsed duration.
    with pytest.raises(ValueError,match='sold_share_seconds'):
        audit_fills(path,metrics)


def test_hash_valid_receipt_with_wrong_cash_is_rejected(tmp_path):
    path=tmp_path/'fills.pt'
    torch.save(dict(ledger=torch.empty(1,0,9),counts=torch.tensor([0])),path)
    metrics=dict(fill_count=[0],cash=[11000])
    with pytest.raises(ValueError,match='cash'):
        audit_fills(path,metrics)
