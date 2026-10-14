from datetime import UTC,date,datetime
from unittest.mock import Mock,patch
import pytest
from services.reference_gateway import ticker_events as gateway,retained_symbol_repair as repair
from services.reference_gateway.providers import MassiveTickerEventsResult


def inputs():
    entity=gateway.normalize_inventory([dict(ticker='NEW',composite_figi='BBG000000001',active=True,
        name='Test',cik='1',primary_exchange='XNAS',currency_name='usd')],[])[0]
    binding=gateway.CanonicalBinding('mapped','security','listing')
    response=MassiveTickerEventsResult('BBG000000001','Test',[
        dict(type='ticker_change',date='2020-01-02',ticker_change=dict(ticker='OLD')),
        dict(type='ticker_change',date='2024-01-02',ticker_change=dict(ticker='NEW'))],'OK','request')
    when=datetime(2026,10,13,tzinfo=UTC);maps={'OLD':binding,'NEW':binding}
    rows,intervals,hash_=gateway.normalize_ticker_event_response(entity,response,binding,
        run_id='fixture',observed_at=when,ticker_bindings=maps)
    coverage=gateway.successful_coverage_row(entity,binding,rows,source_status='completed',response_sha=hash_,
        run_id='fixture',started_at=when,finished_at=when,rows_written=len(rows),rows_deleted=0,stale_after_days=7)
    return entity,binding,rows,intervals,coverage,maps


def test_repair_compiler_matches_gateway_interval_kernel():
    entity,binding,events,expected,coverage,maps=inputs()
    actual=repair.compile_intervals(entity,coverage,events,binding,maps,date(2025,1,1))
    assert [repair.semantic(row) for row in actual]==[repair.semantic(row) for row in expected]


def test_no_historical_alias_can_be_silently_skipped():
    entity,binding,events,expected,coverage,maps=inputs();del maps['OLD']
    with pytest.raises(ValueError,match='no alias will be skipped'):
        repair.compile_intervals(entity,coverage,events,binding,maps,date(2025,1,1))


@pytest.mark.parametrize('field,value',[
    ('source_content_sha256','0'*64),('event_date','2021-01-02'),('ticker','OTHER'),('security_id','other'),
])
def test_changed_retained_evidence_cannot_publish_intervals(field,value):
    entity,binding,events,expected,coverage,maps=inputs();events[0][field]=value
    with pytest.raises(ValueError):repair.compile_intervals(entity,coverage,events,binding,maps,date(2025,1,1))


def test_missing_origin_or_inactive_history_remains_blocked():
    entity,binding,events,expected,coverage,maps=inputs()
    with pytest.raises(ValueError,match='origin'):repair.compile_intervals(entity,coverage,events,binding,maps,date(2019,1,2))
    coverage['mapping_status']='inactive'
    with pytest.raises(ValueError,match='lifecycle'):repair.compile_intervals(entity,coverage,events,binding,maps,date(2025,1,1))


def test_retained_history_must_cover_the_full_end_horizon():
    entity,binding,events,expected,coverage,maps=inputs()
    with pytest.raises(ValueError,match='end horizon'):
        repair.compile_intervals(entity,coverage,events,binding,maps,date(2025,1,1),through=date(2026,10,14))


def test_missing_interval_publication_and_zero_insert_resume_use_same_evidence():
    entity,binding,events,expected,coverage,maps=inputs();stored=[];client=Mock();claim=Mock()
    def read(client,database,table,keys,**options):return events if table==gateway.TICKER_EVENT_TABLE else list(stored)
    def write(client,database,table,rows):stored.extend(rows);return len(rows)
    with patch.object(repair,'verify_sec_write_storage'),\
         patch.object(gateway,'load_ticker_event_inventory',return_value=[entity]),\
         patch.object(gateway,'load_ticker_event_coverage',return_value={entity.provider_entity_key:coverage}),\
         patch.object(gateway,'load_canonical_bindings',return_value={entity.provider_entity_key:binding}),\
         patch.object(gateway,'load_ticker_bindings',return_value={entity.provider_entity_key:maps}),\
         patch.object(gateway,'load_ticker_event_corrections',return_value=()),\
         patch.object(gateway,'query_json_each_row',return_value=[dict(n=1,bytes=len(entity.source_payload_json.encode('utf-8')))]),\
         patch.object(repair,'scoped_rows',side_effect=read),\
         patch.object(gateway,'insert_json_rows',side_effect=write) as inserts:
        first=repair.rebuild(client,database='fixture',identifiers=['BBG000000001'],context_start=date(2025,1,1),claim=claim,execute=True)
        second=repair.rebuild(client,database='fixture',identifiers=['BBG000000001'],context_start=date(2025,1,1),claim=claim,execute=True)
    assert first['inserted']==2 and second['inserted']==0 and first['source_hash']==second['source_hash']
    assert inserts.call_count==1


def test_launcher_retained_repair_is_explicit_and_cannot_enter_frozen_execution():
    from scripts.prepare_certified_research import parse_args
    args=parse_args(['prepare-sources','--end-date','2026-01-02','--repair-retained-symbols','--retained-figi','BBG000000001'])
    assert args.retained_figi==['BBG000000001']
    with pytest.raises(SystemExit):parse_args(['prepare-sources','--end-date','2026-01-02','--repair-retained-symbols'])
    with pytest.raises(SystemExit):parse_args(['resume','--dataset-id','00000000-0000-0000-0000-000000000001',
        '--capacity-profile','pilot.json','--repair-retained-symbols','--retained-figi','BBG000000001'])
