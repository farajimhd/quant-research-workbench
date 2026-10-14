"""Real CH ingestion projection checks with synthetic trades, never SIP files."""
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from pipelines.market_sip.flatfiles import download_update_events as updater, retained_reporting as retained
from pipelines.certified_research.contracts import literal


def projection():
    with patch('sys.argv',['updater','--start-date','2026-01-02','--end-date','2026-01-02','--skip-bars']):
        args=updater.parse_args()
    root=Path(args.flatfiles_root_win)
    job=updater.DownloadJob('trades','2026-01-02','fixture.csv.gz',str(root/'fixture.csv.gz'),1)
    sql=updater.raw_event_union_sql(args,updater.DayFiles('2026-01-02',job,job),trade_only=True,
        include_reporting_reason=True,include_reporting_evidence=True)
    sip=1767362400000000000
    rows=[]
    for index,(conditions,participant) in enumerate((('5,13,30,31,32,33',str(sip-1000000)),('999','0'),('',str(sip-1000000)),('','invalid'))):
        values=dict(ticker='TEST',conditions=conditions,correction='0',exchange='11',id=str(index),
            participant_timestamp=participant,price='1.25',sequence_number=str(index+1),sip_timestamp=str(sip),
            size='1',tape='1',trf_id='',trf_timestamp='')
        rows.append('SELECT '+','.join(literal(value)+' AS '+key for key,value in values.items()))
    call="file("+literal(updater.windows_path_to_clickhouse_path(root/'fixture.csv.gz',root,args.flatfiles_root_ch))+", 'CSVWithNames', "+literal(updater.TRADE_SCHEMA_STRING)+")"
    assert call in sql
    sql=sql.replace(call,'('+' UNION ALL '.join(rows)+')')
    assert 'file(' not in sql
    return sql


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
    finally:
        if created:client.query('DROP DATABASE '+database+' SYNC',read=False)
        client.close()


def test_original_clocks_and_conditions_are_named_not_positional():
    from datetime import date
    sql=retained.raw_insert_sql('SELECT 1',date(2026,1,2))
    assert 'r.*' not in sql
    assert 'r.sip_timestamp_ns,r.participant_timestamp_ns,r.reporting_condition_entries,r.reporting_reason' in sql
    assert '(source_date,ticker,event_meta,' in sql
