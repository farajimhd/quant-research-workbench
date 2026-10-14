from datetime import date
from threading import Event
from unittest.mock import Mock,patch
import json
from contextlib import nullcontext

import pytest

from scripts.prepare_certified_research import parse_args,prepare_sec_share_sources
from pipelines.certified_research import sec_fundamental_preparation as preparation
from pipelines.certified_research.contracts import CampaignConfig
from pipelines.certified_research.contracts import digest


def test_cli_requires_archive_stage_and_chains_fundamental_capture():
    base=['prepare-sources','--end-date','2026-01-02','--capacity-profile','pilot.json',
          '--sec-share-document','320193,0000320193-25-000079,filing.htm','--prepare-sec-fundamental-evidence']
    with pytest.raises(SystemExit):parse_args(base)
    args=parse_args(base+['--verify-sec-archives','--sec-archive-backup-root','G:/sec','--sec-download-receipts','D:/original.jsonl'])
    order=[]
    def archives(*args,**kwargs):order.append('archives');return dict(status='complete')
    def fundamentals(*args,**kwargs):order.append('fundamentals');return dict(status='complete',certified=False)
    with patch('pipelines.certified_research.sec_share_preparation.prepare',return_value=dict(status='complete',captured=1,reused=0,
            results={'0':{'archive_descriptor':{'document':{}}}})),\
         patch('pipelines.certified_research.capacity_profile.load',return_value={}),\
         patch('pipelines.certified_research.sec_archive_preparation.prepare',side_effect=archives),\
         patch.object(preparation,'prepare',side_effect=fundamentals):
        result=prepare_sec_share_sources(args,Mock(),Mock(),Mock(),Mock(),Mock(),Event())
    assert order==['archives','fundamentals'] and result['fundamental_evidence']['certified'] is False


def test_missing_fundamental_pilot_blocks_before_source_fetch_or_ddl():
    from tests.test_certified_sec_share_preparation import profile
    query=Mock()
    with pytest.raises(ValueError,match='sec_fundamental_source'):
        preparation.prepare(query,CampaignConfig(date(2026,1,2),date(2026,1,2)),
            (('320193','0000320193-25-000079','filing.htm'),),Mock(),Mock(),profile(),
            mode='acceptance_checked_day',stop=Event())
    query.assert_not_called()


@pytest.mark.parametrize('readback_ok',(True,False))
def test_bounded_stage_uses_retained_evidence_and_rechecks_before_capture(readback_ok):
    from tests.test_certified_sec_fundamental_publication import source
    from tests.test_certified_sec_share_context import document
    from pipelines.certified_research import sec_share_source,sec_share_context,sec_public_archive_time
    extraction,bulk,witness,qualified,receipt=source()
    lineage=dict(candidate_hash='c'*64,document=dict(content_sha256=extraction['source_text_hash']))
    key=digest(dict(version=sec_share_source.VERSION,candidate_hash=lineage['candidate_hash'],parser=sec_share_context.VERSION))
    witness.pop('witness_hash');witness.update(document_key=key,candidate_hash=lineage['candidate_hash']);witness['witness_hash']=digest(witness)
    qualified=sec_public_archive_time.qualify(witness,receipt)
    def query(sql,**kwargs):
        if 'research_sec_document_archive_time_v1' in sql:return [dict(proof=json.dumps(dict(witness=witness,qualification=qualified)),receipt_hash=qualified['receipt_hash'])]
        if 'research_sec_archive_download_receipt_v1' in sql:return [dict(proof=json.dumps(receipt))]
        if 'source_text FROM' in sql:return [dict(source_text=document())]
        raise AssertionError(sql)
    dictionary=Mock();dictionary.dictionary_database='q_live'
    def capture(*args):
        args[-2]()
        assert args[2]['source_text_hash']==extraction['source_text_hash']
        reconciliation=preparation.sec_fundamental_reconciliation.reconcile(*args[2:7])
        return dict(inserted_rows=3,extraction_hash=extraction['extraction_hash'],reconciliation_hash=reconciliation['reconciliation_hash'])
    def verify(*args):
        args[-1]()
        return dict(readback_verified=readback_ok,extraction_hash=extraction['extraction_hash'],reconciliation_hash=args[2],all_bulk_matched=True)
    with patch.object(preparation.sec_share_preparation,'admission',return_value=lambda size:dict(status='admitted',metadata_bytes=size)),\
         patch.object(preparation.sec_share_capture,'verify'),\
         patch.object(preparation.sec_fundamental_publication,'install'),\
         patch.object(preparation.sec_document_lineage,'inspect',return_value=lineage) as inspect,\
         patch.object(preparation.sec_fundamental_reconciliation,'load_bulk',return_value=tuple(bulk)) as load,\
         patch.object(preparation.sec_fundamental_publication,'capture',side_effect=capture),\
         patch.object(preparation.sec_fundamental_readback,'verify',side_effect=verify),\
         (nullcontext() if readback_ok else pytest.raises(ValueError,match='Independent fundamental readback')):
        result=preparation.prepare(query,CampaignConfig(date(2026,1,2),date(2026,1,2)),
            (('320193','0000320193-25-000079','filing.htm'),),Mock(),lambda:dictionary,{},
            mode='acceptance_checked_day',stop=Event())
    if readback_ok:
        assert result['inserted_rows']==3 and result['certified'] is False and result['readback_verified']
        assert result['matched_documents']==1 and result['unmatched_documents']==0
    assert inspect.call_count==load.call_count==3
    dictionary.close.assert_called_once()
