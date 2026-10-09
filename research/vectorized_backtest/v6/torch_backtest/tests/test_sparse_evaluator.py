import json
import numpy as np
from pathlib import Path
from research.vectorized_backtest.v6.torch_backtest.tests.test_sparse_runner import fixture
from research.vectorized_backtest.v6.torch_backtest import sparse_evaluator,runtime
from research.vectorized_backtest.v6.torch_backtest.sparse_evaluator import SparseSessionEvaluator
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash


def test_complete_session_batches_are_audited_and_resume_without_replay(tmp_path,monkeypatch):
    tape,inputs,space,member,gates=fixture()
    session=dict(day='1970-01-01',start='1970-01-01T00:00:00+00:00',end='1970-01-01T00:01:00+00:00')
    inputs.root=tmp_path/'inputs'/session['day'];inputs.root.mkdir(parents=True)
    (inputs.root/'complete.json').write_text('{}')
    inputs.receipt['identity']['session']=session
    inputs.arrays['market_keys']=inputs.tensors['market_keys'].numpy()
    inputs.arrays['clocks']=tape.clocks.numpy()
    inputs.receipt['files']['market_keys.npy']='synthetic-keys'
    side=tmp_path/'structures'/session['day'];side.mkdir(parents=True)
    np.save(side/'targets.npy',np.full((120,15),np.inf));np.save(side/'valid.npy',np.zeros(120,dtype=bool))
    (side/'complete.json').write_text(json.dumps(dict(status='complete',version='v6-sparse-structural-v1',validation_opened=False,
        input_receipt_sha256=file_hash(inputs.root/'complete.json'),market_keys_sha256='synthetic-keys',listing_ids=[0,1],
        files={n:file_hash(side/n) for n in ('targets.npy','valid.npy')})))
    inputs.compile=lambda members,**kwargs:(gates.clone(),0.)
    monkeypatch.setattr(sparse_evaluator,'SparseInputs',lambda *a,**k:inputs)
    def mkdir(p):p=Path(p);p.mkdir(parents=True,exist_ok=True);return p
    monkeypatch.setattr(runtime,'require_runtime',mkdir);monkeypatch.setattr(sparse_evaluator,'require_runtime',mkdir)
    evaluate=SparseSessionEvaluator(tmp_path/'inputs',tmp_path/'structures',batch_size=1,device='cpu',backend='eager',maximum_fills=512)
    record=evaluate(session,[member,member],tmp_path/'evaluation')
    assert record['financial_audit_passed'] and record['full_session'] and len(record['batch_receipts'])==2
    assert record['candidate_indices']==[0,1]
    assert record['metrics']['fill_count'][0]==record['metrics']['fill_count'][1]>0
    monkeypatch.setattr(sparse_evaluator,'SparseInputs',lambda *a,**k:(_ for _ in ()).throw(AssertionError('Replay should not repeat')))
    assert evaluate(session,[member,member],tmp_path/'evaluation')==record
