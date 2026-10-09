import json
import pytest
import torch
from research.vectorized_backtest.v6.torch_backtest.profile_compact_sessions import compare_sessions


def test_concurrent_qualification_rejects_changed_actual_fill(tmp_path):
    before=tmp_path/'serial';after=tmp_path/'parallel'
    record=dict(day='training',population_sha256='same',candidate_indices=[0],metrics={'net_pnl':[1.]},
        input_receipt_sha256='input',structural_receipt_sha256='structure',batch_receipts=[{'directory':'batch'}])
    for root in (before,after):
        (root/'batch').mkdir(parents=True);(root/'receipt.json').write_text(json.dumps(record))
        torch.save(dict(ledger=torch.ones((1,1,9)),counts=torch.tensor([1])),root/'batch'/'fills.pt')
    assert compare_sessions(before,after)['actual_fill_parity']=='exact'
    changed=torch.ones((1,1,9));changed[0,0,5]+=1
    torch.save(dict(ledger=changed,counts=torch.tensor([1])),after/'batch'/'fills.pt')
    with pytest.raises(ValueError,match='actual fills'):compare_sessions(before,after)
