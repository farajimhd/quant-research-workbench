from unittest.mock import Mock,patch

import pytest

from pipelines.certified_research import sec_original_authority as authority,sec_precision
from pipelines.certified_research.contracts import digest
from tests.test_certified_sec_reported_publication import valid_source


def evidence():
    extraction,*_=valid_source()
    lineage=dict(candidate_hash='a'*64,document=dict(content_sha256=extraction['source_text_hash']))
    projection=sec_precision.select_reported(extraction,mode='round-to-nearest')
    key=digest(dict(version=authority.sec_share_source.VERSION,candidate_hash='a'*64,parser=authority.sec_share_context.VERSION))
    checked=dict(extraction=extraction,readback_verified=True,public_time_qualified=True,binding_hash='b'*64,
        document_key=key,source_document_hash=extraction['source_text_hash'],numeric_mode='round-to-nearest',
        projection_hash=projection['projection_hash'],groups=len(projection['groups']),
        source_reconciliation_hash='c'*64,evidence_hash='d'*64,available_at='2025-10-18 04:00:00.000000',precision=2,
        reconciliation=dict(all_bulk_matched=False,comparison_status_counts={},
            unmatched_original_context_counts={'unscoped':1},parity_input_blockers={'missing_bulk_comparison_census':1}))
    return lineage,checked


def invoke():
    return authority.verify_document(Mock(),'q_live','320193','0000320193-25-000079','filing.htm','b'*64,Mock(),Mock())


def test_explicit_occurrence_survives_both_authority_checks_without_bypassing_binding():
    lineage,checked=evidence()
    occurrence=dict(day='2025-10-18',content_sha256=checked['source_document_hash'],source_version_key='')
    with patch.object(authority.sec_document_lineage,'inspect',return_value=lineage) as inspect,\
         patch.object(authority.sec_reported_readback,'verify',return_value=checked):
        args=(Mock(),'q_live','320193','0000320193-25-000079','filing.htm','b'*64,Mock(),Mock())
        result=authority.verify_document(*args,occurrence=occurrence)
        assert result['document_qualified']
        assert inspect.call_count==2
        assert all(call.kwargs==dict(occurrence=occurrence) for call in inspect.call_args_list)
        checked['document_key']='f'*64
        with pytest.raises(ValueError,match='binding'):
            authority.verify_document(*args,occurrence=occurrence)


@pytest.mark.parametrize('bulk_status', ['missing_bulk_comparison_census','value_mismatch'])
def test_original_values_are_authoritative_and_bulk_disagreements_survive(bulk_status):
    lineage,checked=evidence()
    checked['reconciliation']['parity_input_blockers']={bulk_status:1}
    with patch.object(authority.sec_document_lineage,'inspect',return_value=lineage),\
         patch.object(authority.sec_reported_readback,'verify',return_value=checked):
        result=invoke()
    assert result['document_qualified'] and not result['whole_scope_certified']
    assert result['groups'][0]['value']=='1234'
    assert result['reconciliation']['parity_input_blockers']=={bulk_status:1}
    assert result['aggregation']=='none' and result['cik']=='0000320193'


@pytest.mark.parametrize('field,value', [('public_time_qualified',False),('readback_verified',False),
    ('source_document_hash','f'*64),('document_key','f'*64),('projection_hash','f'*64),('groups',99)])
def test_authority_still_requires_exact_document_time_and_readback(field,value):
    lineage,checked=evidence();checked[field]=value
    with patch.object(authority.sec_document_lineage,'inspect',return_value=lineage),\
         patch.object(authority.sec_reported_readback,'verify',return_value=checked),pytest.raises(ValueError):
        invoke()


def test_authority_blocks_lineage_drift_and_ubs():
    lineage,checked=evidence()
    with patch.object(authority.sec_document_lineage,'inspect',side_effect=[lineage,dict(lineage,candidate_hash='e'*64)]),\
         patch.object(authority.sec_reported_readback,'verify',return_value=checked),pytest.raises(ValueError,match='changed'):
        invoke()
    query=Mock()
    with pytest.raises(ValueError):
        authority.verify_document(query,'q_live','1610520','0000320193-25-000079','filing.htm','b'*64,Mock(),Mock())
    query.assert_not_called()


def test_archive_original_uses_original_values_without_rewriting_live_identity():
    lineage,checked=evidence();original_hash=checked['source_document_hash']
    lineage['document']['content_sha256']='e'*64;lineage['archive_version_key']='f'*64
    checked['source_witness']=dict(version='sec-archive-original-document-witness-v3',
        retained_document_hash='e'*64,candidate_hash=lineage['candidate_hash'],archive_version_key='f'*64,
        cik='320193',accession='0000320193-25-000079',document_name='filing.htm')
    with patch.object(authority.sec_document_lineage,'inspect',return_value=lineage),\
         patch.object(authority.sec_reported_readback,'verify',return_value=checked):
        result=invoke()
        assert result['document_hash']==original_hash and result['retained_document_hash']=='e'*64
        checked['source_witness']['accession']='0000320193-25-000080'
        with pytest.raises(ValueError,match='transport lineage'):invoke()


@pytest.mark.parametrize('broken', [False,True])
def test_complete_tagged_census_is_checked_and_not_whole_document_coverage(broken):
    lineage,checked=evidence();extraction=checked['extraction']
    n=len(extraction['observations'])
    extraction.update(extraction_scope='all_tagged_numeric',whole_document_coverage=False,
        numeric_census=dict(source_facts=n+int(broken),extracted=n,rejected=0))
    extraction.pop('extraction_hash');extraction['extraction_hash']=digest(extraction)
    projection=sec_precision.select_reported(extraction,mode='round-to-nearest')
    checked.update(projection_hash=projection['projection_hash'],groups=len(projection['groups']))
    with patch.object(authority.sec_document_lineage,'inspect',return_value=lineage),\
         patch.object(authority.sec_reported_readback,'verify',return_value=checked):
        if broken:
            with pytest.raises(ValueError,match='census'):invoke()
        else:
            result=invoke()
            assert result['qualification_scope']=='all_tagged_numeric'
            assert result['observations_qualified'] and not result['document_qualified']
            assert not result['whole_document_coverage']
