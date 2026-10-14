from contextlib import ExitStack
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock,patch

import pytest

from pipelines.certified_research import sec_scope_parity as parity,facts
from pipelines.certified_research.contracts import digest


def inputs():
    scope=SimpleNamespace(entity_kind=1,entity_key=1,family='fundamentals',start=date(2026,1,1),end=date(2026,1,2),parent_hash='a'*64,catalog_hash=facts.catalog_hash())
    spec=dict(version='sec-original-scope-specification-v1',entity_key=1,cik='320193',start='2026-01-01',end='2026-01-02',
        source_hash='a'*64,context_start='2025-01-01',forms=['10-K'],metrics=[m.name for m in facts.catalog() if m.tags],
        bindings=[dict(accession='0000320193-25-000079',document='aapl.htm',reconciliation_hash='b'*64,reported_binding_hash='c'*64)])
    return scope,dict(spec,specification_hash=digest(spec))


def stages(stack,*,clock='2026-01-01 04:00:00',ready=True):
    def mock(module,name,result):return stack.enter_context(patch.object(getattr(parity,module),name,return_value=result))
    mock('schema','verify_auxiliary_storage',None)
    mock('sec_coverage','require_ready',dict(coverage_hash='coverage'))
    mock('sec_submission_coverage','inspect',dict(status='retained_evidence_ready',inspection_hash='inspection'))
    mock('sec_filing_census','inventory',dict(documents=[dict(accession='0000320193-25-000079',primary_document='aapl.htm')]))
    mock('reference_sql','sec_source_evidence',dict(source_hash='a'*64,rows=1))
    mock('sec_document_lineage','inspect',dict(document=dict(content_sha256='d'*64),candidate_hash='lineage'))
    mock('sec_fundamental_readback','verify',dict(parity_inputs_ready=ready,all_bulk_matched=True,extraction_hash='extract',
        reconciliation_hash='b'*64,extraction=dict(catalog_hash=facts.catalog_hash(),source_text_hash='d'*64)))
    mock('sec_fundamental_reconciliation','load_bulk',())
    mock('sec_fundamental_reconciliation','compare_original',dict(all_bulk_matched=True,unmatched_original_positions=()))
    mock('sec_reported_readback','verify',dict(source_reconciliation_hash='b'*64,public_time_qualified=True,
        extraction_hash='extract',available_at='2026-01-01 04:00:00',evidence_hash='time'))
    proof=[None]*18;proof[3]='0000320193-25-000079';proof[16]=clock
    import json
    query=Mock(side_effect=[[dict(n=1,bytes=100)],[dict(source_record='sec_core.sec_bulk_mirror_xbrl_fact_v3/fact',proof=json.dumps(proof))]])
    return query


def test_composed_scope_verifier_requires_all_existing_authorities():
    scope,spec=inputs()
    with ExitStack() as stack:
        query=stages(stack)
        result=parity.verify(query,scope,spec,Mock(),Mock())
    assert result['certified'] and result['source_hash']==scope.parent_hash
    assert not result['allocation_certified']
    assert all(call.args[0].startswith('SELECT ') for call in query.call_args_list)


@pytest.mark.parametrize('options,reason',[(dict(clock='2025-12-31 23:00:00'),'precedes proven'),(dict(ready=False),'document parity')])
def test_early_public_clock_and_incomplete_originals_never_certify(options,reason):
    scope,spec=inputs()
    with ExitStack() as stack:
        query=stages(stack,**options)
        with pytest.raises(ValueError,match=reason):parity.verify(query,scope,spec,Mock(),Mock())


def test_forged_or_wrong_scope_specs_fail_before_queries():
    scope,spec=inputs();query=Mock()
    with pytest.raises(ValueError,match='fingerprint'):parity.verify(query,scope,dict(spec,entity_key=2),Mock(),Mock())
    guard=parity.Guard(query,[spec],Mock(),Mock());scope.parent_hash='f'*64
    with pytest.raises(ValueError,match='no frozen'):guard(scope)
    query.assert_not_called()


def test_guard_copies_metadata_and_rejects_competing_specs():
    scope,spec=inputs();guard=parity.Guard(Mock(),[spec],Mock(),Mock())
    spec['bindings'][0]['document']='changed.htm'
    with patch.object(parity,'verify',return_value='verified') as verify:
        assert guard(scope)=='verified'
        assert verify.call_args.args[2]['bindings'][0]['document']=='aapl.htm'
    _,spec=inputs()
    with pytest.raises(ValueError,match='Competing'):parity.Guard(Mock(),[spec,spec],Mock(),Mock())
