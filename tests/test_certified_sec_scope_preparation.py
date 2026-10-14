from contextlib import ExitStack
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock,patch
from uuid import uuid4
import os

import pytest

from pipelines.certified_research import sec_scope_preparation as prep,initial_plan,recipe,runtime_plan
from pipelines.certified_research.campaign import PlannedUnit
from pipelines.certified_research.contracts import CampaignConfig
from pipelines.certified_research.execution import Unit
from tests.test_certified_sec_scope_parity import inputs


def test_discovery_uses_current_lineage_and_stored_banks_without_manual_hashes():
    scope,spec=inputs();query=Mock(side_effect=[[dict(reconciliation_hash='b'*64)],[dict(binding_hash='c'*64)]])
    census=dict(discovery_ready=True,documents=[dict(accession=spec['bindings'][0]['accession'],primary_document='aapl.htm')])
    with patch.object(prep,'required_census_start',return_value=date(2025,1,1)),\
         patch.object(prep.sec_filing_census,'inventory',return_value=census),\
         patch.object(prep.sec_share_capture,'verify'),\
         patch.object(prep.sec_document_lineage,'inspect',return_value=dict(candidate_hash='e'*64,document=dict(content_sha256='d'*64))):
        actual=prep.discover(query,scope,'320193',Mock(),Mock(),forms=('10-K',),numeric_mode='round-to-nearest')
    assert actual==spec
    assert 'research_sec_fundamental_source_document_v2' in query.call_args_list[0].args[0]
    assert "numeric_mode='round-to-nearest'" in query.call_args_list[1].args[0]


@pytest.mark.parametrize('rows',([], [dict(reconciliation_hash='b'*64)]*2))
def test_missing_or_ambiguous_current_source_is_never_selected(rows):
    scope,spec=inputs()
    with patch.object(prep,'required_census_start',return_value=scope.start),\
         patch.object(prep.sec_filing_census,'inventory',return_value=dict(discovery_ready=True,documents=[dict(accession=spec['bindings'][0]['accession'],primary_document='aapl.htm')])),\
         patch.object(prep.sec_share_capture,'verify'),patch.object(prep.sec_document_lineage,'inspect',return_value=dict(candidate_hash='e'*64,document=dict(content_sha256='d'*64))),\
         pytest.raises(ValueError,match='missing or ambiguous'):
        prep.discover(Mock(return_value=rows),scope,'320193',Mock(),Mock())


def test_native_context_demand_includes_earlier_filing_dates_and_rejects_unknowns():
    scope,_=inputs();query=Mock(return_value=[dict(n=1,accessions=1,unknown_dates=0,required_start='2025-12-31')])
    assert prep.required_census_start(query,scope,'320193')==date(2025,12,31)
    assert 'JSONExtractString(proof,4)' in query.call_args.args[0]
    query.return_value=[dict(n=1,accessions=1,unknown_dates=1,required_start=None)]
    with pytest.raises(ValueError,match='filing-date context'):prep.required_census_start(query,scope,'320193')


def test_automatic_preparation_verifies_before_publication_and_preserves_other_units():
    _,spec=inputs();target=PlannedUnit(Unit('fund','fundamentals'),dict(entity_key=1,cik='320193',start=spec['start'],end=spec['end'],source_hash='a'*64))
    other=PlannedUnit(Unit('other','universe'),{})
    with ExitStack() as stack:
        admit=Mock();stack.enter_context(patch.object(prep.sec_share_preparation,'admission',return_value=admit))
        stack.enter_context(patch.object(prep,'discover',return_value=spec))
        verify=stack.enter_context(patch.object(prep.sec_scope_parity,'verify',return_value=dict(certified=True)))
        install=stack.enter_context(patch.object(prep.sec_scope_specification,'install'))
        capture=stack.enter_context(patch.object(prep.sec_scope_specification,'capture',return_value=dict(inserted_rows=2)))
        stack.enter_context(patch.object(prep.sec_scope_specification,'load',return_value=spec))
        result=prep.prepare(Mock(),CampaignConfig(date(2026,1,1),date(2026,1,2)),(target,other),Mock(),Mock(),{},Mock())
        assert result[0].arguments['original_specification_hash']==spec['specification_hash'] and result[1] is other
        verify.side_effect=ValueError('source unqualified');install.reset_mock();capture.reset_mock()
        with pytest.raises(ValueError,match='source unqualified'):prep.prepare(Mock(),CampaignConfig(date(2026,1,1),date(2026,1,2)),(target,),Mock(),Mock(),{},Mock())
        install.assert_not_called();capture.assert_not_called()


def test_initial_assembly_freezes_new_locator_before_capacity_and_rejects_mutation():
    config=CampaignConfig(date(2026,1,2),date(2026,1,2));rules=(dict(token_id=1,modifier_int=0,update_last=1,update_high_low=1,update_volume=1),)
    frozen=recipe.market_recipe(config,rules)
    target=PlannedUnit(Unit('fund','fundamentals'),dict(entity_key=1,cik='320193',start='2025-01-01',end='2026-01-03',source_hash='a'*64))
    plans=(*runtime_plan.units(config,frozen['definition'],rules),target)
    assembled=dict(plans=plans,recipe_hash=frozen['hash'],source_hash='a'*64)
    def prepare(q,c,items,owner,guard):
        return tuple(PlannedUnit(p.unit,dict(p.arguments,original_specification_hash='b'*64)) if p is target else p for p in items)
    with patch('pipelines.certified_research.verification.source_guard',return_value=Mock()),\
         patch.object(runtime_plan,'runtime'),patch('pipelines.certified_research.launch.ExecutionLaunch') as launch:
        initial_plan.build_launch(Mock(),config,str(uuid4()),assembled,Mock(),Mock(),Mock(),Mock(return_value=Mock()),sec_scope_preparer=prepare)
        assert launch.call_args.args[3][-1].arguments['original_specification_hash']=='b'*64
        def mutate(q,c,items,owner,guard):
            items[-1].arguments['source_hash']='c'*64
            return items
        with pytest.raises(ValueError,match='mutated the input graph'):
            initial_plan.build_launch(Mock(),config,str(uuid4()),assembled,Mock(),Mock(),Mock(),Mock(return_value=Mock()),sec_scope_preparer=mutate)


@pytest.mark.skipif(os.environ.get('CERTIFIED_RESEARCH_CLICKHOUSE_TEST')!='1',reason='explicit ClickHouse opt-in')
def test_real_automatic_binding_scope_publication_reuse_and_stored_guard():
    from scripts import build_market_day as b
    from tests.test_certified_sec_reported_publication import valid_source
    from pipelines.certified_research.contracts import digest
    args=b.parse_args(['--date','2026-01-02','--env-file',str(b.ROOT/'.env')]);client=b.Client(args,persistent=False)
    database='certified_research_test_'+uuid4().hex;created=False
    def query(sql,*a,**kw):return client.query(sql.replace('q_live',database),*a,**kw)
    try:
        catalog=(*prep.sec_fundamental_publication.tables(),*prep.sec_reported_publication.tables(),
            *prep.sec_witness_publication.tables(),*prep.sec_scope_specification.tables())
        prep.sec_share_capture.verify(client.query,database,catalog=catalog)
        client.query('CREATE DATABASE '+database,read=False);created=True
        for table in catalog:client.query(table.ddl(database),read=False)
        extraction,bulk,witness,qualification,receipt=valid_source()
        lineage=dict(candidate_hash='e'*64,document=dict(content_sha256=extraction['source_text_hash']))
        key=digest(dict(version=prep.sec_share_source.VERSION,candidate_hash=lineage['candidate_hash'],parser=prep.sec_share_context.VERSION))
        witness.pop('witness_hash');witness.update(document_key=key,candidate_hash=lineage['candidate_hash']);witness['witness_hash']=digest(witness)
        qualification=prep.sec_public_archive_time.qualify(witness,receipt)
        admit=lambda size:dict(status='admitted',metadata_bytes=size,isolated_fixture=True)
        prep.sec_witness_publication.Publisher(query,'q_live',Mock(),Mock(),admit).publish((witness,),(qualification,),receipt)
        original=prep.sec_fundamental_publication.capture(query,'q_live',extraction,bulk,witness,qualification,receipt,Mock(),Mock(),admit)
        reported=prep.sec_reported_publication.capture(query,'q_live',extraction,witness,qualification,receipt,Mock(),Mock(),admit,
            numeric_mode='round-to-nearest',source_reconciliation_hash=original['reconciliation_hash'])
        scope,_=inputs();plan=PlannedUnit(Unit('fund','fundamentals'),dict(entity_key=1,cik='320193',start=str(scope.start),end=str(scope.end),source_hash=scope.parent_hash))
        census=dict(discovery_ready=True,documents=[dict(accession='0000320193-25-000079',primary_document='aapl.htm')]);events=[]
        # Source/completeness qualification is a separate prerequisite; this fixture
        # exercises real stored banks and automatic binding/publication/restoration.
        with patch.object(prep,'required_census_start',return_value=date(2025,1,1)),\
             patch.object(prep.sec_filing_census,'inventory',return_value=census),\
             patch.object(prep.sec_document_lineage,'inspect',return_value=lineage),\
             patch.object(prep.sec_share_preparation,'admission',return_value=admit),\
             patch.object(prep.sec_scope_parity,'verify',return_value=dict(certified=True,source_hash='a'*64)),\
             patch.object(prep.sec_scope_specification,'install'):
            first=prep.prepare(query,CampaignConfig(date(2026,1,1),date(2026,1,2)),(plan,),Mock(),Mock(),{},Mock(),forms=('10-K',),numeric_mode='round-to-nearest',progress=events.append)
            spec=prep.sec_scope_specification.load(query,'q_live',first[0].arguments['original_specification_hash'],Mock(),Mock())
            assert spec['bindings'][0]['reconciliation_hash']==original['reconciliation_hash']
            assert spec['bindings'][0]['reported_binding_hash']==reported['binding_hash']
            prep.prepare(query,CampaignConfig(date(2026,1,1),date(2026,1,2)),(plan,),Mock(),Mock(),{},Mock(),forms=('10-K',),numeric_mode='round-to-nearest',progress=events.append)
            assert [e['inserted_rows'] for e in events if e['state']=='completed']==[2,0]
            with patch.object(prep,'discover',side_effect=AssertionError('resume must not discover')):
                guard=prep.sec_scope_specification.StoredGuard(query,first,Mock(),Mock())
                assert guard(scope)['source_hash']=='a'*64
    finally:
        if created:client.query('DROP DATABASE '+database+' SYNC',read=False)
        client.close()
