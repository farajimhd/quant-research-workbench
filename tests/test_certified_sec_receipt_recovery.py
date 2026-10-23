from copy import deepcopy
from hashlib import sha256,md5
import pytest
from pipelines.certified_research import sec_receipt_recovery as recovery,sec_public_archive_time as timing
from pipelines.certified_research.contracts import digest
from tests.test_certified_sec_public_archive_time import inputs,reseal


def test_downloader_never_finalizes_truncated_transfer(tmp_path,monkeypatch):
    from io import BytesIO
    from unittest.mock import Mock
    from pipelines.sec.edgar import sec_initial_fill_download as downloader
    response=BytesIO(b'partial');response.headers={'Content-Length':'100'}
    monkeypatch.setattr(downloader.request,'urlopen',lambda *a,**k:response)
    coordinator=Mock();coordinator.should_stop.return_value=False
    target=tmp_path/'archive.gz'
    with pytest.raises(RuntimeError,match='incomplete download'):
        downloader.stream_download('https://www.sec.gov/test',target,'test','test',Mock(),10,0,0,1,Mock(),coordinator)
    assert not target.exists() and not list(tmp_path.iterdir())


def evidence(tmp_path):
    data=b'a'*recovery.PART_BYTES+b'complete tail'
    path=tmp_path/'archive.gz';path.write_bytes(data)
    witness,original=inputs()
    etag=md5(md5(data[:recovery.PART_BYTES]).digest()+md5(data[recovery.PART_BYTES:]).digest()).hexdigest()+'-2'
    original.update(byte_size=100,sha256=sha256(data[:100]).hexdigest(),etag='"'+etag+'"')
    repair=dict(status='ok',repair_status='redownloaded_ok',archive_date=original['source_date'],
        archive_bytes=len(data),truncated_by_limit=False,parse_errors=0)
    return path,witness,original,repair


def test_recovery_preserves_original_and_qualifies_exact_complete_representation(tmp_path):
    path,witness,original,repair=evidence(tmp_path);before=deepcopy(original)
    recovered=recovery.recover(path,original,repair)
    assert original==before and recovered['recovery']['original_receipt']==before
    witness.update(container_sha256=recovered['sha256'],archive_bytes=recovered['byte_size']);reseal(witness)
    result=timing.qualify(witness,recovered)
    assert result['public_time_qualified'] and result['receipt_hash']==digest(recovery.canonical(recovered))
    bad=deepcopy(recovered);bad['recovery']['original_receipt']['etag']='changed'
    with pytest.raises(ValueError):timing.qualify(witness,bad)


@pytest.mark.parametrize('field,value',[('etag','"'+'f'*32+'-2"'),('sha256','f'*64),('status','reused')])
def test_invalid_original_never_recovers(tmp_path,field,value):
    path,_,original,repair=evidence(tmp_path);original[field]=value
    with pytest.raises(ValueError):recovery.recover(path,original,repair)


def test_wrong_complete_bytes_and_incomplete_repair_fail(tmp_path):
    path,_,original,repair=evidence(tmp_path)
    with path.open('r+b') as f:f.seek(recovery.PART_BYTES);f.write(b'x')
    with pytest.raises(ValueError):recovery.recover(path,original,repair)
    repair['truncated_by_limit']=True
    with pytest.raises(ValueError):recovery.recover(path,original,repair)


def test_identical_complete_downloads_select_earliest_without_waiving_conflicts():
    _,receipt=inputs()
    later=deepcopy(receipt);later['downloaded_at_utc']='2026-10-23T00:00:00Z'
    assert recovery.select_complete([later,receipt],receipt['byte_size'])==receipt
    bad=deepcopy(later);bad['sha256']='f'*64
    with pytest.raises(ValueError,match='Competing'):
        recovery.select_complete([receipt,bad],receipt['byte_size'])
    assert recovery.select_complete([receipt],receipt['byte_size']+1) is None
