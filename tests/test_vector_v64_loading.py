import hashlib
import json
import pytest
from research.vectorized_backtest.v64.runtime import file_hash
from research.vectorized_backtest.v64.runtime import seed_verified_hashes
from research.vectorized_backtest.v64.prefetch_pipeline import read_results


def test_verified_hash_reuses_unchanged_file_and_invalidates_rewrite(tmp_path,monkeypatch):
    from pathlib import Path
    path=tmp_path/'data';path.write_bytes(b'first')
    expected=hashlib.sha256(b'first').hexdigest()
    assert file_hash(path)==expected
    original=Path.open
    def checked(self,*args,**kwargs):
        if self==path:raise AssertionError('Unchanged file was read again')
        return original(self,*args,**kwargs)
    with monkeypatch.context() as m:
        m.setattr(Path,'open',checked)
        assert file_hash(path)==expected
    path.write_bytes(b'other')
    assert file_hash(path)==hashlib.sha256(b'other').hexdigest()


def test_selective_results_preserve_exact_values_and_integrity(tmp_path):
    receipt=tmp_path/'receipt.json'
    value=dict(program_keys=['a','b'],result_identity={'source':'x'},metrics={'net_pnl':[1.125,-2.75],'trades':[[1,2],[]],'result_reused':[False,True]})
    receipt.write_text(json.dumps(value))
    index=tmp_path/'index.json'
    index.write_text(json.dumps(dict(format='v57-receipt-index-v1',identity=value['result_identity'],receipt=str(receipt),receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest())))
    full=read_results(index)[1]
    selected=read_results(index,['b','absent'])[1]
    assert selected['results']=={'b':full['results']['b']}
    receipt.write_text('{}')
    with pytest.raises(ValueError,match='integrity'):read_results(index,['b'])


def test_controller_receipt_does_not_admit_changed_file(tmp_path):
    p=tmp_path/'bank';p.write_bytes(b'first');s=p.stat()
    record=dict(size=s.st_size,mtime_ns=s.st_mtime_ns,sha256=hashlib.sha256(b'first').hexdigest())
    p.write_bytes(b'changed')
    seed_verified_hashes({str(p):record})
    assert file_hash(p)==hashlib.sha256(b'changed').hexdigest()
