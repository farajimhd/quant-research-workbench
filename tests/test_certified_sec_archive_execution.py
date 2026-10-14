from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock,patch

import pytest

from pipelines.certified_research import sec_archive_execution as execution,sec_archive_staging_plan as planning
from tests.test_certified_sec_archive_staging_plan import prepared


@pytest.mark.parametrize('cached',(False,True))
def test_publication_readback_precedes_cleanup_and_existing_copies_are_preserved(cached):
    with TemporaryDirectory(dir='D:/TradingML/runtimes',prefix='sec-archive-run-') as directory:
        root=Path(directory);backup=root/'backup';cache=root/'cache';relative=Path('2025/QTR4/20251031.nc.tar.gz')
        source=backup/relative;source.parent.mkdir(parents=True);source.write_bytes(b'archive')
        target=cache/relative
        if cached:target.parent.mkdir(parents=True);target.write_bytes(b'archive')
        sources=(prepared('a'*64,'2025-10-31'),prepared('b'*64,'2025-10-31'))
        receipts={relative.as_posix():dict(status='downloaded',byte_size=7,sha256=sha256(b'archive').hexdigest())}
        def verify(path,documents,**kwargs):
            assert Path(path).is_file() and len(documents)==2
            return tuple(dict(witness_hash=key) for key in ('a'*64,'b'*64))
        def qualify(witness,receipt,**kwargs):return dict(qualification_hash=witness['witness_hash'])
        def publish(witnesses,qualifications):
            assert target.is_file()
            return dict(persisted=True,readback_verified=True,witness_hashes=tuple(w['witness_hash'] for w in witnesses),
                qualification_hashes=tuple(q['qualification_hash'] for q in qualifications))
        with patch.object(planning.archive_staging,'validate_roots'),\
             patch.object(execution.sec_archive_witness,'inspect_archives',side_effect=verify) as inspect,\
             patch.object(execution.sec_public_archive_time,'qualify',side_effect=qualify):
            plan=planning.plan(sources,'D:/market-data/sec_core/daily_archives',backup,cache,root)
            result=execution.execute(plan,sources,receipts,Mock(),Event(),publish,Mock(),mode='official_filing_day')
        inspect.assert_called_once()
        assert target.exists()==cached and source.exists()
        assert len(result['preserved_cached_copies'])==int(cached)


def test_unconfirmed_publication_keeps_staged_evidence():
    with TemporaryDirectory(dir='D:/TradingML/runtimes',prefix='sec-archive-stop-') as directory:
        root=Path(directory);backup=root/'backup';cache=root/'cache';relative=Path('2025/QTR4/20251031.nc.tar.gz')
        source=backup/relative;source.parent.mkdir(parents=True);source.write_bytes(b'archive')
        sources=(prepared('a'*64,'2025-10-31'),)
        receipts={relative.as_posix():dict(status='downloaded',byte_size=7,sha256=sha256(b'archive').hexdigest())}
        with patch.object(planning.archive_staging,'validate_roots'),\
             patch.object(execution.sec_archive_witness,'inspect_archives',return_value=({'witness_hash':'a'*64},)),\
             patch.object(execution.sec_public_archive_time,'qualify',return_value={'qualification_hash':'b'*64}):
            plan=planning.plan(sources,'D:/market-data/sec_core/daily_archives',backup,cache,root)
            with pytest.raises(ValueError,match='publication/readback'):
                execution.execute(plan,sources,receipts,Mock(),Event(),lambda *args:None,Mock(),mode='official_filing_day')
        assert (cache/relative).is_file() and source.is_file()


def test_verified_retained_group_skips_copy_and_scan():
    with TemporaryDirectory(dir='D:/TradingML/runtimes',prefix='sec-archive-reuse-') as directory:
        root=Path(directory);backup=root/'backup';cache=root/'cache';relative=Path('2025/QTR4/20251031.nc.tar.gz')
        source=backup/relative;source.parent.mkdir(parents=True);source.write_bytes(b'archive')
        sources=(prepared('a'*64,'2025-10-31'),)
        receipts={relative.as_posix():dict(status='downloaded',byte_size=7,sha256=sha256(b'archive').hexdigest())}
        reuse=Mock(return_value=dict(persisted=True,readback_verified=True,
            witness_hashes=('a'*64,),qualification_hashes=('b'*64,)))
        with patch.object(planning.archive_staging,'validate_roots'),\
             patch.object(execution.archive_staging,'copy_file') as copy,\
             patch.object(execution.sec_archive_witness,'inspect_archives') as inspect:
            plan=planning.plan(sources,'D:/market-data/sec_core/daily_archives',backup,cache,root)
            with patch.object(execution.archive_staging,'space_budget',side_effect=AssertionError('reuse must not require copy headroom')):
                result=execution.execute(plan,sources,receipts,Mock(),Event(),Mock(),Mock(),mode='official_filing_day',reuse=reuse)
        assert result['reused']==(relative.as_posix(),)
        copy.assert_not_called();inspect.assert_not_called()


def test_concrete_adapter_binds_original_receipt_and_reuse():
    from pipelines.certified_research import sec_witness_publication
    receipt=dict(sha256='a'*64,status='downloaded')
    publisher=Mock()
    def run(plan,prepared,receipts,claim,stop,publish,guard,**options):
        assert options['reuse']==publisher.reuse
        publish((dict(container_sha256='a'*64),),({'qualification_hash':'b'*64},))
        return dict(status='complete')
    with patch.object(sec_witness_publication,'Publisher',return_value=publisher),patch.object(execution,'execute',side_effect=run):
        result=execution.publish_retained({},(),{'archive':receipt},Mock(),'q_live',Mock(),Event(),Mock(),Mock(),
            mode='official_filing_day')
    assert result['status']=='complete'
    assert publisher.publish.call_args.args[2] is receipt


def test_cached_digest_uses_bounded_lineage_checks_and_no_copy_admission():
    with TemporaryDirectory(dir='D:/TradingML/runtimes',prefix='sec-cached-hash-') as directory:
        root=Path(directory);backup=root/'backup';cache=root/'cache';relative=Path('2025/QTR4/20251031.nc.tar.gz')
        source=backup/relative;target=cache/relative
        source.parent.mkdir(parents=True);target.parent.mkdir(parents=True)
        data=b'a'*(17*1024**2);source.write_bytes(data);target.write_bytes(data)
        sources=(prepared('a'*64,'2025-10-31'),)
        receipts={relative.as_posix():dict(status='downloaded',byte_size=len(data),sha256=sha256(data).hexdigest())}
        guard=Mock()
        acknowledgement=dict(persisted=True,readback_verified=True,witness_hashes=('a'*64,),qualification_hashes=('b'*64,))
        with patch.object(planning.archive_staging,'validate_roots'),\
             patch.object(execution.sec_archive_witness,'inspect_archives',return_value=({'witness_hash':'a'*64},)),\
             patch.object(execution.sec_public_archive_time,'qualify',return_value={'qualification_hash':'b'*64}):
            plan=planning.plan(sources,'D:/market-data/sec_core/daily_archives',backup,cache,root)
            with patch.object(execution.archive_staging,'space_budget',side_effect=AssertionError('cached input does not allocate a copy')):
                result=execution.execute(plan,sources,receipts,Mock(),Event(),lambda *args:acknowledgement,guard,mode='official_filing_day')
        assert guard.call_count==5
        assert result['preserved_cached_copies']==(str(target),) and target.is_file()
