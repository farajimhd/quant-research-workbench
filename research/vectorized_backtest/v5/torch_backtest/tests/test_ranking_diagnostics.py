import json
import torch
import pytest
from research.vectorized_backtest.v5.torch_backtest.ranking_diagnostics import diagnostics
from research.vectorized_backtest.v5.torch_backtest.runtime import file_hash


def test_receipt_bound_diagnostics_use_candidate_permutation_and_reject_changed_ledger(tmp_path):
    panel=tmp_path/'generation_000';session=panel/'session_000';batch=session/'batch_0000'
    batch.mkdir(parents=True)
    ledger=torch.tensor([[[1,0,0,1,10,2,0,0,0],[2,0,0,-1,10,3,1,0,0]],
                         [[1,0,0,1,10,2,0,0,0],[2,0,0,-1,10,1,1,0,0]]],dtype=torch.float64)
    fills=batch/'fills.pt';torch.save(dict(ledger=ledger,counts=torch.tensor([2,2])),fills)
    receipt=batch/'receipt.json';receipt.write_text(json.dumps(dict(candidate_start=0,candidate_count=2,
        candidate_indices=[1,0],ledger_sha256=file_hash(fills),metrics=dict(closed_positions=[1,1]))))
    session_receipt=session/'receipt.json';session_receipt.write_text(json.dumps(dict(batch_receipts=[dict(directory='batch_0000',sha256=file_hash(receipt))])))
    (panel/'generation.json').write_text(json.dumps(dict(receipts=[dict(path=str(session_receipt),sha256=file_hash(session_receipt))])))
    result=diagnostics(tmp_path,1,[1,2])
    assert result[1]['position_tail_mean_pnl']==-11.
    assert result[2]['position_tail_mean_pnl']==9.
    fills.write_bytes(b'changed')
    with pytest.raises(ValueError,match='ledger changed'):diagnostics(tmp_path,1,[1])
