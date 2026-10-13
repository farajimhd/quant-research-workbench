from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock,patch
from uuid import uuid4

import pytest

from pipelines.certified_research import reference_discovery as discovery,facts


def fixture():
    from tests.test_certified_initial_preparation import inputs
    initial,snapshot,_,_=inputs()
    snapshot=dict(snapshot,context_start=date(2025,12,1),end=date(2026,1,2))
    issuer=initial['issuers'][0].issuer_key;security=initial['securities'][0].security_key
    dataset=str(uuid4())
    receipts=[dict(dataset_id=dataset,entity_kind=2,entity_key=security,family=family,range_start='2026-01-02',
        range_end='2026-01-03',parent_hash='a'*64,catalog_hash=facts.catalog_hash(),status=2,output_rows=0,
        attempt_id=str(uuid4()),output_hash='b'*64,published_at='2026-01-04 00:00:00.000000')
        for family in ('corporate_actions','float_allocation')]
    def query(sql):
        if 'id_issuer_identifier_v1' in sql:return [dict(issuer_id='issuer',ciks=['0000000123','123'])]
        if 'research_reference_receipt_v1' in sql:return receipts
        raise AssertionError('Unexpected metadata query: '+sql)
    client=SimpleNamespace(query=Mock(return_value=[dict(n=1,unique_events=1,unknown_public_time=0,
        invalid_public_time=0,missing_evidence=0,source_hash='c'*64)]),close=Mock())
    args=dict(query=query,config=initial['config'],dataset_id=dataset,claim=Mock(),client_factory=lambda:client,
        source_guard=Mock(),snapshot=snapshot,days=(date(2026,1,2),),fundamental_start=date(2026,1,2),short_start=date(2025,12,1))
    return args,receipts,client,issuer,security


@pytest.mark.parametrize('include_supply',(False,True))
def test_discovery_builds_typed_inputs_from_one_metadata_batch_and_prepublication_short_audits(include_supply):
    args,receipts,client,issuer,security=fixture()
    if include_supply:receipts.append(dict(receipts[0],family='reported_supply'))
    progress=[]
    with patch.object(discovery.schema,'verify_storage'),patch.object(discovery.schema,'verify_auxiliary_storage'),\
         patch.object(discovery.sec_coverage,'require_ready'),\
         patch.object(discovery.short_sql,'publication_coverage',return_value=dict(ready=True,coverage_hash='d'*64)),\
         patch.object(discovery.reference_sql,'sec_source_evidence',return_value=dict(source_hash='e'*64)),\
         patch.object(discovery,'ReferencePublisher') as publisher:
        publisher.return_value.receipt.side_effect=lambda scope:next(r for r in receipts if r['family']==scope.family)
        result=discovery.discover(**args,progress=progress.append)
    assert result['ready'] and not result['missing_authorities']
    assert result['issuers'][0].issuer_key==issuer and result['issuers'][0].cik=='123'
    assert result['securities'][0].security_key==security
    assert len(result['prerequisites'])==(6 if include_supply else 5)
    assert bool(result['securities'][0].reported_supply_parent)==include_supply
    assert all(p.unit.parents==('universe',) for p in result['prerequisites'] if p.unit.product.endswith('_acquisition'))
    assert all('research_membership_v1' not in c.args[0] for c in client.query.call_args_list)
    assert client.close.call_count==3
    assert progress and any(p['status']=='prepared' for p in progress)


def test_missing_authorities_are_reported_before_opening_workers_or_fabricating_inputs():
    args,receipts,client,_,security=fixture();receipts.clear()
    with patch.object(discovery.schema,'verify_storage'),patch.object(discovery.schema,'verify_auxiliary_storage'),\
         patch.object(discovery.sec_coverage,'require_ready'),\
         patch.object(discovery.short_sql,'publication_coverage',return_value=dict(ready=True,coverage_hash='d'*64)):
        result=discovery.discover(**args)
    assert not result['ready'] and not result['prerequisites']
    assert {r['family'] for r in result['missing_authorities']}=={'corporate_actions','float_allocation'}
    assert all(r['security_key']==security for r in result['missing_authorities'])
    client.query.assert_not_called();client.close.assert_not_called()


def test_short_context_cannot_use_a_snapshot_clipped_to_target_dates():
    args,_,_,_,_=fixture();args['snapshot']=dict(args['snapshot'],context_start=args['config'].start)
    with pytest.raises(ValueError,match='context range'):discovery.discover(**args)


def test_qualified_donor_scopes_create_explicit_capacity_classified_imports():
    args,receipts,client,_,_=fixture()
    donors=[dict(r,dataset_id=str(uuid4())) for r in receipts]
    def query(sql):
        if 'id_issuer_identifier_v1' in sql:return [dict(issuer_id='issuer',ciks=['123'])]
        if 'dataset_id!=' in sql:return donors
        if 'research_reference_receipt_v1' in sql:return []
        raise AssertionError(sql)
    args['query']=query
    with patch.object(discovery.schema,'verify_storage'),patch.object(discovery.schema,'verify_auxiliary_storage'),\
         patch.object(discovery.sec_coverage,'require_ready'),\
         patch.object(discovery.short_sql,'publication_coverage',return_value=dict(ready=True,coverage_hash='d'*64)),\
         patch.object(discovery.reference_sql,'sec_source_evidence',return_value=dict(source_hash='e'*64)),\
         patch.object(discovery,'ReferencePublisher') as publisher:
        publisher.return_value.receipt.side_effect=lambda scope:next(r for r in donors if r['family']==scope.family)
        result=discovery.discover(**args)
    assert result['ready']
    imports=[p for p in result['prerequisites'] if p.unit.product=='reference_import']
    assert len(imports)==2
    from pipelines.certified_research import capacity
    assert 'reference_import' in capacity.WRITE_PRODUCTS
    for plan in result['prerequisites']:
        if plan.unit.product in ('corporate_actions','float_allocation'):
            assert len(plan.unit.parents)==1 and 'import_parent_hash' in plan.arguments
