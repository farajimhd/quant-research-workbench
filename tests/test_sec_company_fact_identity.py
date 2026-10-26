import json,os
from uuid import uuid4
import pytest
from pipelines.sec.edgar.sec_pipeline.company_fact_identity import PREFIX,SORTING_KEY,corrected_ddl,verify


DDL="""CREATE TABLE q_live.sec_xbrl_company_fact_v3 UUID '00000000-0000-0000-0000-000000000001'
(
 company_fact_id String,cik String,taxonomy String,tag String,unit_code String,
 period_end_date Nullable(Date),accession_number Nullable(String),source_content_sha256 String,
 value Float64,inserted_at DateTime64(3,'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(ifNull(period_end_date,toDate('1970-01-01')))
ORDER BY ("""+PREFIX+""")
SETTINGS storage_policy='live_market_ssd'
"""


def test_ddl_correction_preserves_columns_partition_policy_and_rejects_unknown_key():
    ddl=corrected_ddl(DDL,'scratch','facts')
    assert 'CREATE TABLE `scratch`.`facts`' in ddl and 'UUID' not in ddl
    assert 'ORDER BY ('+SORTING_KEY+')' in ddl and 'PRIMARY KEY ('+PREFIX+')' in ddl
    assert "storage_policy='live_market_ssd'" in ddl
    assert "PARTITION BY toYYYYMM(ifNull(period_end_date,toDate('1970-01-01')))" in ddl
    with pytest.raises(ValueError):corrected_ddl(DDL.replace('ORDER BY ('+PREFIX+')','ORDER BY cik'),'scratch','facts')
    with pytest.raises(ValueError):corrected_ddl(DDL,'scratch; DROP TABLE x','facts')


def test_writer_guard_rejects_lossy_key_before_insert():
    class Client:
        def __init__(self,key):self.key=key
        def execute(self,sql):return json.dumps(dict(sorting_key=self.key,engine='ReplacingMergeTree'))
    with pytest.raises(ValueError,match='loses fact/source identity'):verify(Client(PREFIX),'q_live')
    verify(Client(SORTING_KEY),'q_live')


def test_live_duration_and_source_vintages_survive_replacing_and_reuse():
    if os.environ.get('FUNDAMENTALS_LIVE_TEST')!='1':pytest.skip('Explicit SSD integration opt-in')
    from scripts.run_fundamentals import Client
    from research.mlops.env import load_env_files
    from research.mlops.clickhouse import discover_clickhouse_env_files
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    c=Client('http://192.168.1.218:18123');db='fundamentals_validation_sec_identity_'+uuid4().hex[:8]
    try:
        c.query('CREATE DATABASE '+db,read=False)
        legacy=corrected_ddl(DDL,db,'legacy').replace('ORDER BY ('+SORTING_KEY+')','ORDER BY ('+PREFIX+')')
        c.query(legacy,read=False);c.query(corrected_ddl(DDL,db,'corrected'),read=False)
        rows=[dict(company_fact_id=period,cik='0000000001',taxonomy='us-gaap',tag='Revenue',unit_code='USD',period_end_date='2026-06-30',accession_number='0000000001-26-000001',source_content_sha256=sha,value=value,inserted_at='2026-07-01 00:00:00.000') for period,sha,value in [('three','a'*64,10),('six','a'*64,20),('three','b'*64,11),('six','b'*64,21)]]
        payload='\n'.join(json.dumps(x) for x in rows)
        for table in ['legacy','corrected']:c.query('INSERT INTO '+db+'.'+table+' FORMAT JSONEachRow\n'+payload,read=False)
        assert c.query('SELECT count() n FROM '+db+'.legacy FINAL')==[dict(n=1)]
        assert c.query('SELECT count() n FROM '+db+'.corrected FINAL')==[dict(n=4)]
        c.query('INSERT INTO '+db+'.corrected FORMAT JSONEachRow\n'+payload,read=False)
        assert c.query('SELECT count() n FROM '+db+'.corrected FINAL')==[dict(n=4)]
        verify(c.http,db,'corrected')
        assert c.query("SELECT DISTINCT disk_name FROM system.parts WHERE active AND database='"+db+"'")==[dict(disk_name='live_market_ssd')]
    finally:c.query('DROP DATABASE IF EXISTS '+db+' SYNC',read=False);c.close()


def test_full_row_snapshot_keeps_distinct_raw_variants_and_deduplicates_retries():
    if os.environ.get('FUNDAMENTALS_LIVE_TEST')!='1':pytest.skip('Explicit SSD integration opt-in')
    from scripts.run_fundamentals import Client
    from research.mlops.env import load_env_files
    from research.mlops.clickhouse import discover_clickhouse_env_files
    from pipelines.sec.edgar import sec_companyfact_identity_migration as migration
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    c=Client('http://192.168.1.218:18123');db='fundamentals_validation_sec_snapshot_'+uuid4().hex[:8]
    try:
        c.query('CREATE DATABASE '+db,read=False)
        columns=['company_fact_id','cik','taxonomy','tag','unit_code','period_end_date','accession_number','source_content_sha256','value','inserted_at']
        ddl=migration.snapshot_ddl(DDL,'snapshot',columns).replace('`q_live`.`snapshot`','`'+db+'`.`snapshot`')
        c.query(ddl,read=False)
        rows=[dict(company_fact_id='three',cik='0000000001',taxonomy='us-gaap',tag='Revenue',unit_code='USD',period_end_date='2026-06-30',accession_number='0000000001-26-000001',source_content_sha256='a'*64,value=value,inserted_at=stamp) for value,stamp in [(10,'2026-07-01 00:00:00.000'),(11,'2026-07-02 00:00:00.000')]]
        payload='\n'.join(json.dumps(x) for x in rows)
        for _ in range(2):c.query('INSERT INTO '+db+'.snapshot FORMAT JSONEachRow\n'+payload,read=False)
        assert c.query('SELECT count() n FROM '+db+'.snapshot FINAL')==[dict(n=2)]
        sql=migration.fingerprint_sql('snapshot',"_partition_id='202606'",columns,snapshot=True).replace('q_live.snapshot',db+'.snapshot')
        proof=c.query(sql)[0]
        assert proof['n']==1 and proof['conflicting_source_values']==1
    finally:c.query('DROP DATABASE IF EXISTS '+db+' SYNC',read=False);c.close()


def test_live_restart_safe_preparation_atomic_cutover_and_ack_recovery(tmp_path,monkeypatch):
    if os.environ.get('FUNDAMENTALS_LIVE_TEST')!='1':pytest.skip('Explicit SSD integration opt-in')
    from scripts.run_fundamentals import Client
    from research.mlops.env import load_env_files
    from research.mlops.clickhouse import discover_clickhouse_env_files
    from pipelines.sec.edgar import sec_companyfact_identity_migration as migration
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    c=Client('http://192.168.1.218:18123');db='fundamentals_validation_sec_cutover_'+uuid4().hex[:8];root=tmp_path/'identity-migration'
    monkeypatch.setattr(migration,'DATABASE',db);monkeypatch.setattr(migration.platform,'node',lambda:'DESKTOP-SAAI85T')
    def run(mode):
        monkeypatch.setattr('sys.argv',['migration','--run-dir',str(root),'--mode',mode,'--workers','2'])
        assert migration.main()==0
    try:
        c.query('CREATE DATABASE '+db,read=False)
        legacy=corrected_ddl(DDL,db,migration.SOURCE).replace('ORDER BY ('+SORTING_KEY+')','ORDER BY ('+PREFIX+')')
        c.query(legacy,read=False)
        rows=[dict(company_fact_id=period,cik='0000000001',taxonomy='us-gaap',tag='Revenue',unit_code='USD',period_end_date='2026-06-30',accession_number='0000000001-26-000001',source_content_sha256=sha,value=value,inserted_at='2026-07-01 00:00:00.000') for period,sha,value in [('three','a'*64,10),('six','a'*64,20),('three','b'*64,11),('six','b'*64,21)]]
        c.query('INSERT INTO '+db+'.'+migration.SOURCE+' SETTINGS optimize_on_insert=0 FORMAT JSONEachRow\n'+'\n'.join(json.dumps(x) for x in rows),read=False)
        original=migration.table_info(c.query,migration.SOURCE)['uuid']
        run('prepare');run('prepare')
        assert migration.table_info(c.query,migration.SOURCE)['uuid']==original
        request=json.loads((root/'request.json').read_text(encoding='utf-8'))
        assert c.query('SELECT count() n FROM '+db+'.'+request['snapshot']+' FINAL')==[dict(n=4)]
        run('cutover')
        assert c.query('SELECT count() n FROM '+db+'.'+migration.SOURCE+' FINAL')==[dict(n=4)]
        assert migration.table_info(c.query,request['target'])['uuid']==original
        (root/'completion.json').rename(root/'completion-before-simulated-ack-loss.json')
        run('verify-cutover')
        completion=json.loads((root/'completion.json').read_text(encoding='utf-8'))
        assert completion['post_cutover_source_containment']
        assert not (root/'STOP').exists()
    finally:c.query('DROP DATABASE IF EXISTS '+db+' SYNC',read=False);c.close()
