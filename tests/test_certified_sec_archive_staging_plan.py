from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from pipelines.certified_research import sec_archive_staging_plan as staging
from pipelines.certified_research.contracts import digest


def prepared(key,day):
    year,month,_=day.split('-');quarter=(int(month)-1)//3+1
    document=dict(source_archive_path=f'D:/market-data/sec_core/daily_archives/{year}/QTR{quarter}/{day.replace("-","")}.nc.tar.gz',source_archive_date=day)
    body=dict(document=dict(document_key=key,lineage=dict(document=document)))
    return dict(body,prepared_hash=digest(body))


def test_same_archive_documents_share_one_copy_and_chunks_preserve_reserve():
    with TemporaryDirectory(dir='D:/TradingML/runtimes',prefix='sec-staging-plan-') as directory:
        root=Path(directory);backup=root/'backup';cache=root/'cache';folder=backup/'2025'/'QTR4';folder.mkdir(parents=True)
        (folder/'20251031.nc.tar.gz').write_bytes(b'a'*300)
        (folder/'20251103.nc.tar.gz').write_bytes(b'b'*400)
        sources=(prepared('a'*64,'2025-10-31'),prepared('b'*64,'2025-10-31'),prepared('c'*64,'2025-11-03'))
        with patch.object(staging.archive_staging,'validate_roots'),patch.object(staging.archive_staging,'space_budget',
            return_value=dict(usable_bytes=500,reserve_bytes=200,free_bytes=700)):
            result=staging.plan(sources,'D:/market-data/sec_core/daily_archives',backup,cache,root,maximum_chunk_bytes=0)
        assert result['documents']==3 and result['unique_archives']==2
        assert len(result['archives'][0]['documents'])==2
        assert len(result['chunks']['chunks'])==2 and result['chunks']['total_bytes']==700
        assert result['copy_started'] is False and not cache.exists()


def test_existing_cache_is_planned_without_allocating_copy_headroom():
    with TemporaryDirectory(dir='D:/TradingML/runtimes',prefix='sec-cached-plan-') as directory:
        root=Path(directory);backup=root/'backup';cache=root/'cache';relative=Path('2025/QTR4/20251031.nc.tar.gz')
        for base in (backup,cache):
            target=base/relative;target.parent.mkdir(parents=True);target.write_bytes(b'archive')
        with patch.object(staging.archive_staging,'validate_roots'),patch.object(staging.archive_staging,'space_budget',
            return_value=dict(usable_bytes=0,reserve_bytes=200,free_bytes=100)):
            result=staging.plan((prepared('a'*64,'2025-10-31'),),'D:/market-data/sec_core/daily_archives',backup,cache,root)
        assert result['cached_unverified']==(relative.as_posix(),)
        assert result['chunks']['copy_bytes']==0 and result['chunks']['total_bytes']==7
        assert result['chunks']['chunks']==(((relative.as_posix(),7),),)


def test_verified_publication_reuse_is_planned_without_a_d_copy():
    with TemporaryDirectory(dir='D:/TradingML/runtimes',prefix='sec-reused-plan-') as directory:
        root=Path(directory);backup=root/'backup';cache=root/'cache';relative=Path('2025/QTR4/20251031.nc.tar.gz')
        target=backup/relative;target.parent.mkdir(parents=True);target.write_bytes(b'archive')
        with patch.object(staging.archive_staging,'validate_roots'),patch.object(staging.archive_staging,'space_budget',
            return_value=dict(usable_bytes=0,reserve_bytes=200,free_bytes=100)):
            result=staging.plan((prepared('a'*64,'2025-10-31'),),'D:/market-data/sec_core/daily_archives',backup,cache,root,
                reuse_lookup=lambda archives:(relative.as_posix(),))
        assert result['publication_reuse']==(relative.as_posix(),) and result['chunks']['copy_bytes']==0
        assert not cache.exists()


def test_path_traversal_and_staging_outside_runtime_are_rejected():
    runtime=Path('D:/TradingML/runtimes')
    with pytest.raises(ValueError,match='below'):
        staging.plan((prepared('a'*64,'2025-10-31'),),'D:/market-data/sec_core/daily_archives',
            'G:/market-data/sec_core/daily_archives',runtime,runtime)
    source=prepared('a'*64,'2025-10-31');source['document']['lineage']['document']['source_archive_path']='D:/market-data/sec_core/daily_archives/../other/file.tar.gz'
    source.pop('prepared_hash');source['prepared_hash']=digest(source)
    with patch.object(staging.archive_staging,'validate_roots'),pytest.raises(ValueError,match='traversal'):
        staging.plan((source,),'D:/market-data/sec_core/daily_archives',runtime/'backup',runtime/'cache',runtime)
