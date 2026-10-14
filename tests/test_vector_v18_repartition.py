import json
from pathlib import Path
import numpy as np
from research.vectorized_backtest.v18 import prepare_enriched as preparation
from research.vectorized_backtest.v18.repartition import migrate_enriched
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash


def test_rebinding_preserves_values_and_original_receipt(tmp_path):
    source=tmp_path/'source';source.mkdir();np.save(source/'values.npy',np.array([[1.,np.nan]],dtype=np.float32))
    receipt=dict(status='complete',definition=preparation.definition(),input_sha256='old-input',validation_opened=False,files={'values.npy':file_hash(source/'values.npy')})
    (source/'complete.json').write_text(json.dumps(receipt));original=file_hash(source/'complete.json')
    prior=tmp_path/'prior';folder=prior/'research/vectorized_backtest/v18';folder.mkdir(parents=True)
    current=Path(preparation.__file__)
    for name in ('prepare_enriched.py','enriched_math.py'):(folder/name).write_bytes(current.with_name(name).read_bytes())
    target=tmp_path/'target';migrate_enriched(source,target,'new-input','validation',prior)
    result=preparation.verify(target,'new-input','validation')
    assert result['repartition_provenance']['source_receipt_sha256']==original
    assert result['repartition_provenance']['source_validation_opened'] is False
    assert file_hash(source/'complete.json')==original
    assert file_hash(source/'values.npy')==file_hash(target/'values.npy')
