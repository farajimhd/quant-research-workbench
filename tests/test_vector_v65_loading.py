import hashlib
import json
import pytest
from research.vectorized_backtest.v65.runtime import file_hash
from research.vectorized_backtest.v65.runtime import seed_verified_hashes
from research.vectorized_backtest.v65.prefetch_pipeline import read_results


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


def test_same_size_rewrites_with_restored_mtime_never_reuse_stale_hash(tmp_path):
    import os
    path=tmp_path/'same-stat';path.write_bytes(b'first');s=path.stat()
    for i in range(30):
        value=i.to_bytes(5,'little');path.write_bytes(value)
        os.utime(path,ns=(s.st_atime_ns,s.st_mtime_ns))
        assert file_hash(path)==hashlib.sha256(value).hexdigest()


def test_unsupported_change_journal_reads_contents_instead_of_trusting_timestamps(tmp_path,monkeypatch):
    from research.vectorized_backtest.v65 import runtime
    monkeypatch.setattr(runtime,'change_version',lambda p:None)
    path=tmp_path/'fallback';path.write_bytes(b'first')
    assert runtime.file_hash(path)==hashlib.sha256(b'first').hexdigest()
    path.write_bytes(b'other')
    assert runtime.file_hash(path)==hashlib.sha256(b'other').hexdigest()


def test_seed_receipt_binds_change_sequence_not_just_timestamp(tmp_path):
    import os
    from research.vectorized_backtest.v65.file_version import change_version
    path=tmp_path/'seed';path.write_bytes(b'first');s=path.stat();version=change_version(path)
    record=dict(size=s.st_size,mtime_ns=s.st_mtime_ns,sha256=hashlib.sha256(b'first').hexdigest(),
                change_version=list(version) if version else None)
    path.write_bytes(b'other');os.utime(path,ns=(s.st_atime_ns,s.st_mtime_ns))
    seed_verified_hashes({str(path):record})
    assert file_hash(path)==hashlib.sha256(b'other').hexdigest()
