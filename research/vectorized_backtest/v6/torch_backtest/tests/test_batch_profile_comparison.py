import json
import pytest
import torch
from research.vectorized_backtest.v6.torch_backtest.profile_compact_sessions import compare_sessions
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash


def test_partition_comparison_preserves_candidate_fills_and_rejects_changes(tmp_path):
    def create(name,partitions,change=False):
        root=tmp_path/name;root.mkdir();batches=[]
        for number,indices in enumerate(partitions):
            folder=root/str(number);folder.mkdir()
            ledger=torch.tensor([[[float(i+int(change)),2.]] for i in indices])
            torch.save(dict(counts=torch.ones(len(indices),dtype=torch.int64),ledger=ledger),folder/'fills.pt')
            (folder/'receipt.json').write_text(json.dumps(dict(candidate_indices=indices,ledger_sha256=file_hash(folder/'fills.pt'))))
            batches.append(dict(directory=folder.name,sha256=file_hash(folder/'receipt.json')))
        record=dict(day='training',population_sha256='same',candidate_indices=[0,1,2],metrics={},input_receipt_sha256='input',structural_receipt_sha256='structure',batch_receipts=batches)
        (root/'receipt.json').write_text(json.dumps(record));return root
    reference=create('reference',[[0],[1],[2]])
    actual=create('actual',[[0,1],[2]])
    assert compare_sessions(reference,actual)['actual_fill_parity']=='exact'
    with pytest.raises(ValueError,match='actual fills'):
        compare_sessions(reference,create('changed',[[0,1,2]],True))
