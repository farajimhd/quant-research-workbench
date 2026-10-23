from hashlib import sha256
from unittest.mock import Mock,patch

import pytest

from pipelines.certified_research import sec_share_source as source
from tests.test_certified_sec_share_context import document


def prepared(payload):
    return dict(candidate_hash='a'*64,document=dict(content_sha256=sha256(payload.encode()).hexdigest()))


def test_prepares_normalized_contexts_once_and_preserves_conflicting_values():
    extra=document('9').split('<ix:nonFraction',1)[1].split('</ix:nonFraction>',1)[0]
    payload=document().replace('</html>','<ix:nonFraction'+extra.replace('id="f"','id="f2"')+'</ix:nonFraction></html>')
    # Both facts use one context; conflicting values must survive extraction.
    query=Mock(side_effect=[[{'content_sha256':sha256(payload.encode()).hexdigest(),'bytes':len(payload.encode())}],[{'source_text':payload}]])
    with patch.object(source.sec_document_lineage,'inspect',return_value=prepared(payload)):
        result=source.prepare(query,'320193','0000320193-25-000079','filing.htm',Mock(),Mock())
    assert len(result['contexts'])==1 and len(result['observations'])==2
    assert {r['value'] for r in result['observations']}=={'1234','9'}
    assert len({r['context_key'] for r in result['observations']})==1
    assert result['candidate_only'] and not result['allocation_certified']
    assert all(call.args[0].startswith('SELECT ') for call in query.call_args_list)


def test_rejected_fact_is_kept_without_inventing_a_context_link():
    payload=document('12,34')
    query=Mock(side_effect=[[{'content_sha256':sha256(payload.encode()).hexdigest(),'bytes':len(payload.encode())}],[{'source_text':payload}]])
    with patch.object(source.sec_document_lineage,'inspect',return_value=prepared(payload)):
        result=source.prepare(query,'320193','0000320193-25-000079','filing.htm',Mock(),Mock())
    assert result['contexts']==()
    row=result['observations'][0]
    assert row['value'] is None and row['context_key'] is None
    assert row['rejected_context_id']=='c' and row['reason']=='invalid_share_number'


def test_byte_admission_prevents_payload_fetch():
    payload=document();query=Mock(return_value=[{'content_sha256':sha256(payload.encode()).hexdigest(),'bytes':100000}])
    with patch.object(source.sec_document_lineage,'inspect',return_value=prepared(payload)),pytest.raises(ValueError,match='byte bound'):
        source.prepare(query,'320193','0000320193-25-000079','filing.htm',Mock(),Mock(),maximum_bytes=1000)
    assert query.call_count==1


def test_changed_lineage_after_extraction_is_not_returned():
    payload=document();before=prepared(payload)
    query=Mock(side_effect=[[{'content_sha256':sha256(payload.encode()).hexdigest(),'bytes':len(payload.encode())}],[{'source_text':payload}]])
    with patch.object(source.sec_document_lineage,'inspect',side_effect=[before,dict(before,candidate_hash='b'*64)]),pytest.raises(ValueError,match='changed'):
        source.prepare(query,'320193','0000320193-25-000079','filing.htm',Mock(),Mock())
