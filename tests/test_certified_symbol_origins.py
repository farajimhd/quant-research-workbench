from dataclasses import replace
from datetime import UTC,date,datetime
from unittest.mock import Mock,patch
import pytest
from pipelines.certified_research import historical_identity as identity
from services.reference_gateway import ticker_events as gateway,retained_symbol_repair as repair
from services.reference_gateway.providers import MassiveTickerEventsResult


def inputs(status='OK'):
    entity=gateway.normalize_inventory([dict(ticker='TEST',composite_figi='BBG000000001',active=True,
        name='Fixture',cik='1',primary_exchange='XNAS',currency_name='usd',type='CS')],[])[0]
    binding=gateway.CanonicalBinding('mapped','security','listing');when=datetime(2026,10,13,tzinfo=UTC)
    events,_,hash_=gateway.normalize_ticker_event_response(entity,MassiveTickerEventsResult(
        entity.provider_identifier,entity.entity_name,[],status,'fixture'),binding,run_id='fixture',observed_at=when)
    coverage=gateway.successful_coverage_row(entity,binding,events,source_status='covered_empty',response_sha=hash_,
        run_id='fixture',started_at=when,finished_at=when,rows_written=0,rows_deleted=0,stale_after_days=7)
    origin=identity.ListingObservation(date(2025,1,2),'TEST','BBG000000001','','0000000001',
        'XNAS','USD','CS',True,date(2020,1,1),None)
    return entity,binding,coverage,origin


def test_verified_empty_history_can_start_only_at_a_verified_dated_point():
    entity,binding,coverage,origin=inputs()
    rows=repair.compile_intervals(entity,coverage,[],binding,{'TEST':binding},date(2025,2,1),origin=origin,through=date(2026,10,2))
    assert len(rows)==1 and rows[0]['valid_from_date']=='2025-01-02'
    assert rows[0]['mapping_status']=='origin_verified_scope_pending'
    assert rows[0]['valid_from_date']!=str(origin.list_date) # No IPO-date backprojection.
    with pytest.raises(ValueError,match='origin'):
        repair.compile_intervals(entity,coverage,[],binding,{'TEST':binding},date(2025,2,1))


@pytest.mark.parametrize('change',[
    dict(exchange='XNYS'),dict(cik='0000000002'),dict(provider_type='ADRC'),
    dict(as_of=date(2025,3,1)),dict(active=False),
])
def test_origin_cannot_cross_issuer_venue_type_or_context_boundaries(change):
    entity,binding,coverage,origin=inputs()
    with pytest.raises(ValueError,match='origin'):
        repair.compile_intervals(entity,coverage,[],binding,{'TEST':binding},date(2025,2,1),origin=replace(origin,**change))


def test_not_found_cannot_be_promoted_to_covered_empty():
    entity,binding,coverage,origin=inputs('NOT_FOUND')
    with pytest.raises(ValueError,match='successful response signature'):
        repair.compile_intervals(entity,coverage,[],binding,{'TEST':binding},date(2025,2,1),origin=origin)


def test_origin_batch_is_one_query_and_requires_exact_immutable_evidence():
    entity,binding,coverage,origin=inputs();request=dict(as_of=str(origin.as_of),ticker='TEST',composite_figi=entity.composite_figi,cik='1')
    stored=identity.row(origin);query=Mock(return_value=[stored])
    with patch('pipelines.certified_research.schema.verify_auxiliary_storage'):
        result=identity.load_origins(query,'arte',[request],context_start=date(2025,2,1),figis={entity.composite_figi})
    assert result=={entity.composite_figi:origin} and query.call_count==1
    query.return_value=[dict(stored,evidence_hash='0'*64)]
    with patch('pipelines.certified_research.schema.verify_auxiliary_storage'),pytest.raises(ValueError,match='hash'):
        identity.load_origins(query,'arte',[request],context_start=date(2025,2,1),figis={entity.composite_figi})


def test_symbol_origin_does_not_bypass_historical_listing_admission():
    from pipelines.certified_research import universe
    row=dict(listing_id='listing',security_id='security',issuer_id='issuer',ticker='TEST',conids=['123'],
        exchange_code='XNAS',exchange_country='US',currency_code='USD',product_type='STK',
        symbol_interval_id='origin',interval_mapping='origin_verified_scope_pending',evidence_hash='a'*64)
    result=universe.resolve([row],date(2025,1,1),date(2026,10,2))
    assert not result['ready'] and result['rejection_counts']==dict(dated_origin_requires_listing_scope=1)
