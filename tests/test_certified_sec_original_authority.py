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
