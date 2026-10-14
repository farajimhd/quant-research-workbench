import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock,patch

import pytest

from pipelines.certified_research import sec_archive_preparation as preparation
from pipelines.certified_research.contracts import CampaignConfig
from datetime import date
from scripts.prepare_certified_research import parse_args,prepare_sec_share_sources


def test_receipt_ledger_selects_only_original_successes_and_rejects_conflicts():
    key='2025/QTR4/20251031.nc.tar.gz'
    row=dict(source_kind='daily_feed_archive',status='downloaded',source_url='https://www.sec.gov/Archives/edgar/Feed/'+key,
        source_date='2025-10-31',downloaded_at_utc='2026-06-12T07:46:29Z',byte_size=1234,sha256='a'*64,
        last_modified='Sat, 01 Nov 2025 02:10:54 GMT')
    with TemporaryDirectory(dir='D:/TradingML/runtimes',prefix='sec-ledger-test-') as root:
        path=Path(root)/'original.jsonl'
        path.write_text(json.dumps(dict(row,status='reused'))+'\n'+json.dumps(row)+'\n',encoding='utf-8')
        assert preparation.receipts(path,({'archive_key':key},))[key]==row
        with path.open('a',encoding='utf-8') as out:out.write(json.dumps(dict(row,sha256='b'*64))+'\n')
        with pytest.raises(ValueError,match='Competing'):preparation.receipts(path,({'archive_key':key},))


def test_cli_requires_explicit_archive_sources_and_runs_stage_after_candidates():
    base=['prepare-sources','--end-date','2026-01-02','--capacity-profile','pilot.json',
          '--sec-share-document','320193,0000320193-25-000079,filing.htm','--verify-sec-archives']
    with pytest.raises(SystemExit):parse_args(base)
    args=parse_args(base+['--sec-archive-backup-root','G:/sec','--sec-download-receipts','D:/original.jsonl'])
    with patch('pipelines.certified_research.sec_share_preparation.prepare',return_value=dict(status='complete',captured=1,reused=0)),\
         patch('pipelines.certified_research.capacity_profile.load',return_value={}),\
         patch.object(preparation,'prepare',return_value=dict(status='complete')) as archive:
        result=prepare_sec_share_sources(args,Mock(),Mock(),Mock(),Mock(),Mock(),Event())
    assert result['archive_evidence']['status']=='complete'
    assert archive.call_args.kwargs['reserve_bytes']==256*1024**3
    assert archive.call_args.kwargs['mode']=='acceptance_checked_day'


def test_missing_archive_capacity_blocks_before_payload_fetch_or_ddl():
    from tests.test_certified_sec_share_preparation import profile
    with patch.object(preparation.sec_share_source,'prepare') as source,\
         patch.object(preparation.sec_witness_publication,'install') as install,pytest.raises(ValueError,match='sec_archive_witness'):
        preparation.prepare(Mock(),CampaignConfig(date(2026,1,2),date(2026,1,2)),
            (('320193','0000320193-25-000079','filing.htm'),),Mock(),Mock(),profile(),canonical_root='D:/sec',
            backup_root='G:/sec',cache_root='D:/TradingML/runtimes/cache',runtime_root='D:/TradingML/runtimes',
            receipt_ledger='D:/original.jsonl',mode='acceptance_checked_day',stop=Event(),reserve_bytes=256*1024**3,
            reserve_fraction=.1,maximum_chunk_bytes=256*1024**3)
    source.assert_not_called();install.assert_not_called()
