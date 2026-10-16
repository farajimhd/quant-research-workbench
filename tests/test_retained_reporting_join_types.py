"""Real CH ingestion projection checks with synthetic trades, never SIP files."""
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from pipelines.market_sip.flatfiles import download_update_events as updater, retained_reporting as retained
from pipelines.certified_research.contracts import literal


def projection(*,listing_tape=False):
    with patch('sys.argv',['updater','--start-date','2026-01-02','--end-date','2026-01-02','--skip-bars']):
        args=updater.parse_args()
    root=Path(args.flatfiles_root_win)
    job=updater.DownloadJob('trades','2026-01-02','fixture.csv.gz',str(root/'fixture.csv.gz'),1)
    sql=updater.raw_event_union_sql(args,updater.DayFiles('2026-01-02',job,job),trade_only=True,
        include_reporting_reason=True,include_reporting_evidence=True,include_listing_tape=listing_tape)
    sip=1767362400000000000
    rows=[]
    for index,(conditions,participant) in enumerate((('5,13,30,31,32,33',str(sip-1000000)),('999','0'),('',str(sip-1000000)),('','invalid'))):
        values=dict(ticker='TEST',conditions=conditions,correction='0',exchange='11',id=str(index),
            participant_timestamp=participant,price='1.25',sequence_number=str(index+1),sip_timestamp=str(sip),
            size='1',tape=('1','','0','invalid')[index] if listing_tape else '1',trf_id='',trf_timestamp='')
        rows.append('SELECT '+','.join(literal(value)+' AS '+key for key,value in values.items()))
    call="file("+literal(updater.windows_path_to_clickhouse_path(root/'fixture.csv.gz',root,args.flatfiles_root_ch))+", 'CSVWithNames', "+literal(updater.TRADE_SCHEMA_STRING)+")"
    assert call in sql
    sql=sql.replace(call,'('+' UNION ALL '.join(rows)+')')
    assert 'file(' not in sql
    return sql


@pytest.mark.skipif(os.environ.get('CERTIFIED_RESEARCH_CLICKHOUSE_TEST')!='1',reason='Explicit bounded SSD ClickHouse opt-in')
def test_original_tape_preserves_missing_and_zero_instead_of_nyse():
    from scripts import build_market_day as builder
    client=builder.Client(builder.parse_args(['--date','2026-01-02','--env-file',str(builder.ROOT/'.env')]),persistent=False)
    try:
        rows=client.query('SELECT sequence_number,original_listing_tape,bitAnd(bitShiftRight(event_meta,3),7) encoded_tape '
            'FROM ('+projection(listing_tape=True)+') ORDER BY sequence_number')
        assert [r['original_listing_tape'] for r in rows]==[1,None,0,None]
        assert [r['encoded_tape'] for r in rows]==[0,0,0,0]
    finally:client.close()


@pytest.mark.skipif(os.environ.get('CERTIFIED_RESEARCH_CLICKHOUSE_TEST')!='1',reason='Explicit bounded SSD ClickHouse opt-in')
def test_ingestion_array_materialization_preserves_full_conditions_and_unknown_clocks():
    from scripts import build_market_day as builder
    from pipelines.certified_research.schema import verify_auxiliary_storage
    client=builder.Client(builder.parse_args(['--date','2026-01-02','--env-file',str(builder.ROOT/'.env')]),persistent=False)
    database='certified_research_test_'+uuid4().hex
    created=False
    try:
        client.query('CREATE DATABASE '+database,read=False);created=True
        client.query(retained.raw_table().ddl(database),read=False)
        raw=projection()
        from datetime import date
        client.query(retained.raw_insert_sql(raw,date(2026,1,2),database=database),read=False)
        rows=client.query(f'SELECT sequence_number,event_meta,sip_timestamp_ns,participant_timestamp_ns,reporting_condition_entries,reporting_reason '
            f'FROM {database}.{retained.RAW} ORDER BY sequence_number')
        assert len(rows)==4
        assert all(int(row['sip_timestamp_ns'])==1767362400000000000 for row in rows)
        assert int(rows[0]['participant_timestamp_ns'])==1767362399999000000
        assert rows[0]['reporting_condition_entries']==['5','13','30','31','32','33']
        assert int(rows[0]['event_meta'])&192==192
        assert int(rows[1]['event_meta'])&192==0 and rows[1]['reporting_reason']==8
        assert int(rows[1]['participant_timestamp_ns'])==0
        assert int(rows[2]['event_meta'])&192==64
        assert rows[3]['participant_timestamp_ns'] is None and rows[3]['reporting_reason']==8
        assert int(rows[3]['event_meta'])&192==0
        verify_auxiliary_storage(client.query,database,(retained.RAW,))
        # Compare direct reconciliation with one materialized canonical scan.
        from pipelines.market_sip.events import reporting_evidence as ns
        from dataclasses import replace
        source=replace(retained.canonical_table(),name='original_fixture')
        client.query(source.ddl(database),read=False)
        columns=[c.name for c in source.columns]
        values=['toDate(\'2026-01-02\')' if c=='source_date' else 'toUInt64(sequence_number*10)' if c=='ordinal'
            else 'bitAnd(event_meta,63)' if c=='event_meta' else c for c in columns]
        client.query(f'INSERT INTO {database}.{source.name} ({",".join(columns)}) SELECT {",".join(values)} '
            f'FROM {database}.{retained.RAW}',read=False)
        client.query(retained.canonical_table().ddl(database),read=False)
        first=1767362400000000
        client.query(retained.canonical_insert_sql(database+'.'+source.name,date(2026,1,2),first,first,database=database),read=False)
        fields=[c for c in columns if c not in ('source_date','ordinal','ticker','event_meta')]
        direct=ns.reconciled_projection(f'SELECT * FROM {database}.{retained.RAW}',database+'.'+source.name,date(2026,1,2),
            'a'*64,fields,first_sip_us=first,last_sip_us=first,repair_reporting_flags=True)
        cached=ns.reconciled_projection(f'SELECT * FROM {database}.{retained.RAW}',database+'.'+retained.CANONICAL,date(2026,1,2),
            'a'*64,fields,first_sip_us=first,last_sip_us=first,repair_reporting_flags=True,canonical_stage=True)
        assert sorted(client.query(direct),key=lambda r:r['ordinal'])==sorted(client.query(cached),key=lambda r:r['ordinal'])
        assert all(r['mismatch']==0 and r['ambiguous']==0 for r in client.query(cached))
        # A canonical snapshot may contain old and new reporting bits while a
        # mutation replaces parts. Neither state may change ordinal matching.
        client.query(f'ALTER TABLE {database}.{retained.CANONICAL} UPDATE '
            'event_meta=bitXor(event_meta,192) WHERE ordinal%20=0 SETTINGS mutations_sync=2',read=False)
        assert sorted(client.query(direct),key=lambda r:r['ordinal'])==sorted(client.query(cached),key=lambda r:r['ordinal'])
        assert all(r['mismatch']==0 and r['ambiguous']==0 for r in client.query(cached))
        verify_auxiliary_storage(client.query,database,(retained.CANONICAL,))
        client.query(ns.ddl(database,retained.STAGE,stage=True),read=False)
        client.query(ns.ddl(database,ns.TABLE),read=False)
        client.query(f'INSERT INTO {database}.{retained.STAGE} '+cached,read=False)
        log=[]
        def traced(sql,**kwargs):log.append(sql);return client.query(sql,**kwargs)
        retained.publish_evidence(traced,date(2026,1,2),lambda:None,database=database,emit=lambda _:None)
        assert int(client.query(f'SELECT count() n FROM {database}.{ns.TABLE}')[0]['n'])==4
        inserts=[sql for sql in log if sql.startswith('INSERT')]
        assert len(inserts)==16 and all('cityHash64(ticker)%16=' in sql for sql in inserts)
        assert sum('SHA256(' in sql for sql in log)==2
        assert not any('EXCEPT ALL' in sql for sql in log)
        # Resume inserts no duplicates and ignores another source date.
        from pipelines.certified_research.publication import insert_rows
        record=client.query(f'SELECT * FROM {database}.{ns.TABLE} LIMIT 1')[0]
        insert_rows(client.query,database,ns.TABLE,[dict(record,source_date='2026-01-03')])
        retained.publish_evidence(client.query,date(2026,1,2),lambda:None,database=database,emit=lambda _:None)
        assert int(client.query(f'SELECT count() n FROM {database}.{ns.TABLE}')[0]['n'])==5
        client.query(f"ALTER TABLE {database}.{ns.TABLE} UPDATE sip_timestamp_ns=sip_timestamp_ns+1 WHERE source_date='2026-01-02' SETTINGS mutations_sync=2",read=False)
        with pytest.raises(ValueError,match='differs from reconciled'):
            retained.publish_evidence(client.query,date(2026,1,2),lambda:None,database=database,emit=lambda _:None)
    finally:
        if created:client.query('DROP DATABASE '+database+' SYNC',read=False)
        client.close()


def test_original_clocks_and_conditions_are_named_not_positional():
    from datetime import date
    sql=retained.raw_insert_sql('SELECT 1',date(2026,1,2))
    assert 'r.*' not in sql
    assert 'r.sip_timestamp_ns,r.participant_timestamp_ns,r.reporting_condition_entries,r.reporting_reason' in sql
    assert '(source_date,ticker,event_meta,' in sql


def test_date_pruning_includes_utc_rollover_and_query_budget_is_frozen():
    from datetime import datetime,timezone,date
    from pipelines.market_sip.events.reporting_evidence import event_date_predicate
    from pipelines.certified_research.contracts import CampaignConfig
    first=int(datetime(2026,1,2,23,59,tzinfo=timezone.utc).timestamp()*1000000)
    assert event_date_predicate(first,first+120000000)=="event_date BETWEEN toDate('2026-01-02') AND toDate('2026-01-03')"
    old=CampaignConfig(date(2026,1,1),date(2026,10,2))
    bounded=CampaignConfig(date(2026,1,1),date(2026,10,2),query_timeout_seconds=900)
    assert 'query_timeout_seconds' not in old.identity()['config']
    assert old.content_hash!=bounded.content_hash and old.calculation_hash==bounded.calculation_hash
    with pytest.raises(ValueError,match='bounded'):CampaignConfig(date(2026,1,1),date(2026,10,2),query_timeout_seconds=0)


@pytest.mark.parametrize('deadline',('0','-1','3601'))
def test_launcher_rejects_unbounded_or_invalid_deadlines_before_connection(deadline):
    from scripts.prepare_certified_research import parse_args
    with pytest.raises(SystemExit):parse_args(['schema','--query-timeout-seconds',deadline])
