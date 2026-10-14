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
