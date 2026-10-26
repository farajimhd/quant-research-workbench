"""Receipt-preserving SEC Company Facts replacing-key migration.

Default is read-only planning. Preparation retains all distinct raw rows in a
full-row-identity snapshot, then builds and verifies the corrected canonical
table. Cutover is explicit and atomic; the old table and snapshot remain intact.
No source records, availability clocks or fundamentals receipts are rewritten.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import platform
import re
import threading
import time

from pipelines.certified_research.contracts import digest, literal
from pipelines.sec.edgar.sec_pipeline.company_fact_identity import PREFIX, SORTING_KEY, corrected_ddl
from pipelines.sec.edgar.sec_pipeline.company_fact_identity import verify as verify_identity
from pipelines.sec.edgar.sec_pipeline.clickhouse_writer import verify_sec_write_storage
from pipelines.sec.edgar.sec_text_layout import normalized_clickhouse_key
from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files
from scripts.run_fundamentals import Client, save, lock

DATABASE = 'q_live'
SOURCE = 'sec_xbrl_company_fact_v3'
VERSION = 'sec-companyfacts-full-identity-migration-v1'


def table_info(query, table):
    rows = query("SELECT uuid,engine,sorting_key,primary_key,create_table_query FROM system.tables "
                 f"WHERE database={literal(DATABASE)} AND name={literal(table)}")
    if len(rows) != 1:
        raise ValueError('SEC migration table is absent or ambiguous: '+table)
    return rows[0]


def snapshot_key(columns):
    # Direct prefix columns are already present. Nullable dates/accessions still
    # appear as raw columns as well as their prefix expressions, retaining NULL.
    return PREFIX+', '+', '.join(c for c in columns if c not in ('cik','taxonomy','tag','unit_code'))


def snapshot_ddl(ddl, table, columns):
    result = corrected_ddl(ddl, DATABASE, table)
    result = result.replace('ORDER BY ('+SORTING_KEY+')',
                            'ORDER BY ('+snapshot_key(columns)+')')
    if not re.search(r'\ballow_nullable_key\s*=', result):
        result = result.rstrip().rstrip(';')+', allow_nullable_key = 1'
    return result


def scope_sql(partition, shard, shards):
    return f'_partition_id={literal(partition)} AND modulo(cityHash64(cik),{shards})={shard}'


def fingerprint_sql(table, scope, columns, *, snapshot=False):
    business = [c for c in columns if c not in ('source_run_id', 'inserted_at')]
    row = 'tuple('+','.join(business)+')'
    if snapshot:
        inner = (f'SELECT argMax({row},inserted_at) row,uniqExact(value) variants '
                 f'FROM {DATABASE}.{table} FINAL PREWHERE {scope} GROUP BY {SORTING_KEY}')
    else:
        inner = f'SELECT {row} row,1 variants FROM {DATABASE}.{table} FINAL PREWHERE {scope}'
    return ('SELECT count() n,countIf(variants>1) conflicting_source_values,'
            'toString(sumWithOverflow(cityHash64(row))) h1,'
            'toString(sumWithOverflow(sipHash64(row))) h2 FROM ('+inner+
            ') SETTINGS max_bytes_before_external_group_by=268435456')


def missing_sql(source, target, scope, columns):
    row = 'tuple('+','.join(c for c in columns if c not in ('source_run_id','inserted_at'))+')'
    def side(table):
        return (f'SELECT company_fact_id id,source_content_sha256 sha,cityHash64({row}) h1,'
                f'sipHash64({row}) h2 FROM {DATABASE}.{table} FINAL PREWHERE {scope}')
    return f'SELECT count() n FROM ({side(source)}) s LEFT ANTI JOIN ({side(target)}) t USING(id,sha,h1,h2)'


def post_cutover(query, legacy, columns):
    parts=query(f'SELECT partition_id,sum(rows) n FROM system.parts WHERE active AND database={literal(DATABASE)} AND table={literal(legacy)} GROUP BY partition_id ORDER BY partition_id')
    work=[(r['partition_id'],i,max(1,(int(r['n'])+249999)//250000)) for r in parts for i in range(max(1,(int(r['n'])+249999)//250000))]
    # Cutover checks are bounded by the same partition/issuer shards. Sequential
    # metadata queries here also allow any already-started source insert to drain.
    for part,shard,shards in work:
        if query(missing_sql(legacy,SOURCE,scope_sql(part,shard,shards),columns))[0]['n']:
            raise ValueError('Legacy source has a late row absent from the corrected canonical table; preserve both tables')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir', required=True, type=Path)
    p.add_argument('--endpoint', default='http://192.168.1.218:18123')
    p.add_argument('--mode', choices=('plan','prepare','cutover','verify-cutover'), default='plan')
    p.add_argument('--workers', type=int, default=4)
    a = p.parse_args()
    root = a.run_dir.resolve()
    if not root.is_relative_to(Path('D:/TradingML/runtimes').resolve()) or not 1 <= a.workers <= 4:
        raise ValueError('Use the operational runtime root and 1..4 bounded verification lanes')
    if a.mode != 'plan' and platform.node().upper() != 'DESKTOP-SAAI85T':
        raise ValueError('SEC canonical migration is workstation-owned')
    load_env_files(discover_clickhouse_env_files(), verbose=False)
    root.mkdir(parents=True, exist_ok=True)
    c = Client(a.endpoint)
    started = time.monotonic()
    mutex = threading.Lock()
    state = dict(state='planning',mode='SEC fact-identity migration',unit='cohorts',
                 counts=dict(published=0,unchanged=0,audited=0,failed=0),active={},queued=0,
                 workers=a.workers,total=0,counts_include_active=True)
    def status(**updates):
        state.update(updates,elapsed=time.monotonic()-started,updated_at=datetime.now(timezone.utc).isoformat())
        save(root/'status.json',state)
    def guard():
        if (root/'STOP').exists():
            raise InterruptedError('Graceful stop requested; preserve tables and completed receipts')
    heartbeat_stop=threading.Event()
    def heartbeat():
        monitor=Client(a.endpoint)
        try:
            while not heartbeat_stop.wait(5):
                if state.get('snapshot_table') and 'snapshot' in state['active']:
                    try:
                        progress=monitor.query("SELECT read_rows,read_bytes,memory_usage FROM system.processes "
                            "WHERE startsWith(upper(trimLeft(query)),'INSERT') AND position(query,"+
                            literal(state['snapshot_table'])+")>0 LIMIT 1")
                        if progress:
                            r=progress[0];total=state['source_rows']
                            with mutex:
                                if 'snapshot' in state['active']:
                                    state['active']['snapshot']=dict(stage=f"Snapshot read {r['read_rows']:,}/{total:,} rows; {r['read_bytes']/1024**3:.1f} GiB; {r['memory_usage']/1024**2:.0f} MiB",**r)
                    except Exception:
                        pass  # A missing observation never changes job ownership/state.
                with mutex:status()
        finally:monitor.close()
    heartbeat_thread=threading.Thread(target=heartbeat,daemon=True)
    owned=False
    try:
        with lock(root/'controller.lock'):
            owned=True
            heartbeat_thread.start()
            if (root/'STOP').exists():
                raise ValueError('STOP retained; diagnose and drain the prior owner before resuming')
            current = table_info(c.query,SOURCE)
            old = json.loads((root/'request.json').read_text(encoding='utf-8')) if (root/'request.json').exists() else None
            if old:
                request=old
            else:
                if current['engine']!='ReplacingMergeTree':
                    raise ValueError('Reviewed SEC source must be ReplacingMergeTree')
                if normalized_clickhouse_key(current['sorting_key']) != normalized_clickhouse_key(PREFIX):
                    raise ValueError('Source key is not the reviewed legacy key')
                columns=[r['name'] for r in c.query(f'SELECT name FROM system.columns WHERE database={literal(DATABASE)} AND table={literal(SOURCE)} ORDER BY position')]
                if not all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',name) for name in columns):
                    raise ValueError('Unreviewed SEC source column identifiers')
                if not {'company_fact_id','source_content_sha256','value','inserted_at'}<=set(columns):
                    raise ValueError('SEC source lacks fact identity/provenance columns')
                token=digest((VERSION,current['uuid'],str(root)))[:12]
                request=dict(version=VERSION,source=current,columns=columns,
                    snapshot=SOURCE+'_snapshot_'+token,target=SOURCE+'_identity_'+token)
                request['request_hash']=digest(request)
            body=dict(request);seal=body.pop('request_hash')
            if digest(body)!=seal or request['version']!=VERSION:
                raise ValueError('SEC migration request seal differs')
            snapshot,target=request['snapshot'],request['target']
            columns=request['columns']
            if current['uuid'] != request['source']['uuid']:
                target_before=table_info(c.query,target)
                if (normalized_clickhouse_key(current['sorting_key'])==normalized_clickhouse_key(SORTING_KEY)
                        and target_before['uuid']==request['source']['uuid'] and (root/'verification.json').exists()):
                    verify_sec_write_storage(c.http,DATABASE,[SOURCE,snapshot,target])
                    if not (root/'completion.json').exists():
                        if a.mode!='verify-cutover':
                            raise ValueError('Cutover applied; explicit post-cutover verification is required before completion')
                        post_cutover(c.query,target,columns)
                        proof=json.loads((root/'verification.json').read_text(encoding='utf-8'))
                        proof.update(legacy_table=target,completed_at=datetime.now(timezone.utc).isoformat(),post_cutover_source_containment=True)
                        save(root/'completion.json',proof)
                    status(state='complete',qualification_status='Atomic cutover already applied; legacy table preserved')
                    return 0
                raise ValueError('Canonical source identity changed outside this migration')
            if a.mode=='verify-cutover':
                raise ValueError('Atomic cutover has not been applied')
            if current['create_table_query']!=request['source']['create_table_query']:
                raise ValueError('Canonical source schema changed outside this migration')
            verify_sec_write_storage(c.http,DATABASE,[SOURCE])
            db=c.query(f'SELECT engine FROM system.databases WHERE name={literal(DATABASE)}')
            if db != [dict(engine='Atomic')]:raise ValueError('Atomic database required for verified cutover')
            parts=c.query(f'SELECT partition_id,sum(rows) n,sum(bytes_on_disk) bytes FROM system.parts WHERE active AND database={literal(DATABASE)} AND table={literal(SOURCE)} GROUP BY partition_id ORDER BY partition_id')
            work=[(r['partition_id'],i,max(1,(int(r['n'])+249999)//250000)) for r in parts for i in range(max(1,(int(r['n'])+249999)//250000))]
            save(root/'plan.json',dict(request_hash=seal,source_rows=sum(int(r['n']) for r in parts),source_bytes=sum(int(r['bytes']) for r in parts),partitions=len(parts),verification_cohorts=len(work),parts=parts))
            if a.mode=='plan':
                print(json.dumps(dict(mode='plan',source_rows=sum(int(r['n']) for r in parts),source_bytes=sum(int(r['bytes']) for r in parts),partitions=len(parts),cohorts=len(work))))
                return 0
            save(root/'request.json',request)
            if a.mode=='prepare':
                needed=3*sum(int(r['bytes']) for r in parts)+64*1024**3
                free=c.query("SELECT free_space FROM system.disks WHERE name='live_market_ssd'")
                if len(free)!=1 or int(free[0]['free_space'])<needed:raise ValueError('Insufficient explicit SSD migration reserve')
                for name,ddl in [(snapshot,snapshot_ddl(request['source']['create_table_query'],snapshot,columns)),
                                 (target,corrected_ddl(request['source']['create_table_query'],DATABASE,target))]:
                    exists=c.query(f'SELECT name FROM system.tables WHERE database={literal(DATABASE)} AND name={literal(name)}')
                    if not exists:c.query(ddl,read=False)
                    verify_sec_write_storage(c.http,DATABASE,[name])
                verify_identity(c.http,DATABASE,target)
                if normalized_clickhouse_key(table_info(c.query,snapshot)['sorting_key']) != normalized_clickhouse_key(snapshot_key(columns)):
                    raise ValueError('Raw snapshot does not preserve the full-row identity')
                # Full-row identity makes an interrupted copy restart-safe without
                # truncation: exact duplicates collapse, all distinct raw rows stay.
                if not (root/'snapshot-complete.json').exists():
                    status(state='running',total=len(work),queued=len(work),snapshot_table=snapshot,
                           source_rows=sum(int(r['n']) for r in parts),
                           qualification_status='Capture all distinct raw rows; no source FINAL or shared-merge pause',
                           active={'snapshot':{'stage':'server-side immutable raw snapshot'}})
                    snapshot_receipts=root/'snapshot-partitions';snapshot_receipts.mkdir(exist_ok=True)
                    for part in parts:
                        guard()
                        partition=part['partition_id']
                        receipt=snapshot_receipts/(partition+'.json')
                        if receipt.exists():
                            proof=json.loads(receipt.read_text(encoding='utf-8'))
                            if proof.get('request_hash')!=seal or proof.get('table_uuid')!=table_info(c.query,snapshot)['uuid']:
                                raise ValueError('Snapshot partition receipt identity differs')
                            continue
                        c.query(f'INSERT INTO {DATABASE}.{snapshot} SELECT * FROM {DATABASE}.{SOURCE} PREWHERE _partition_id={literal(partition)} SETTINGS max_bytes_before_external_sort=268435456',read=False)
                        save(receipt,dict(partition=partition,request_hash=seal,table_uuid=table_info(c.query,snapshot)['uuid']))
                    save(root/'snapshot-complete.json',dict(table=snapshot,uuid=table_info(c.query,snapshot)['uuid']))
                if json.loads((root/'snapshot-complete.json').read_text(encoding='utf-8'))['uuid']!=table_info(c.query,snapshot)['uuid']:
                    raise ValueError('Raw snapshot identity differs from completed receipt')
                verify_sec_write_storage(c.http,DATABASE,[snapshot,target])
                guard()
                status(state='running',total=len(work),queued=len(work),active={},qualification_status='Copy and exactly verify corrected fact/source identities')
            else:
                if not (root/'verification.json').exists():raise ValueError('Verified preparation is required before cutover')
                verify_sec_write_storage(c.http,DATABASE,[snapshot,target])
                verify_identity(c.http,DATABASE,target)
                status(state='running',total=len(work),queued=len(work),active={},qualification_status='Recheck exact data and canonical-source containment before atomic cutover')
            receipts=root/'receipts';receipts.mkdir(exist_ok=True)
            def verify_work(item):
                partition,shard,shards=item
                key=partition+'-'+str(shard)+'of'+str(shards)
                q=Client(a.endpoint)
                try:
                    guard()
                    with mutex:
                        state['active'][key]=dict(stage='copy/readback' if a.mode=='prepare' else 'cutover integrity',started=time.monotonic()-started);status()
                    scope=scope_sql(partition,shard,shards)
                    path=receipts/(key+'.json')
                    if a.mode=='prepare' and not path.exists():
                        q.query(f'INSERT INTO {DATABASE}.{target} SELECT * FROM {DATABASE}.{snapshot} FINAL PREWHERE {scope} SETTINGS max_bytes_before_external_sort=268435456',read=False)
                    expected=q.query(fingerprint_sql(snapshot,scope,columns,snapshot=True))[0]
                    actual=q.query(fingerprint_sql(target,scope,columns))[0]
                    if int(expected['conflicting_source_values']) or actual!=expected:
                        raise ValueError('Full fact/source identity readback or same-source numeric integrity differs: '+key)
                    missing=q.query(missing_sql(SOURCE,target,scope,columns))[0]['n']
                    if missing:raise ValueError('Canonical source gained uncopied records; retain all tables and reconcile: '+key)
                    save(path,dict(request_hash=seal,scope=scope,fingerprint=actual))
                    with mutex:
                        state['active'].pop(key);state['counts']['audited']+=1;state['queued']-=1;status()
                    return actual
                finally:q.close()
            results=[]
            with ThreadPoolExecutor(max_workers=a.workers) as pool:
                for future in as_completed([pool.submit(verify_work,item) for item in work]):results.append(future.result())
            if table_info(c.query,SOURCE)['uuid']!=request['source']['uuid']:
                raise ValueError('Canonical source UUID changed during verification')
            proof=dict(request_hash=seal,cohorts=len(results),corrected_rows=sum(int(r['n']) for r in results),
                       full_raw_snapshot_preserved=True,exact_two_fingerprints=True,source_containment_verified=True,
                       target_uuid=table_info(c.query,target)['uuid'],snapshot_uuid=table_info(c.query,snapshot)['uuid'],
                       elapsed=time.monotonic()-started)
            proof['legacy_visible_rows']=c.query(f'SELECT count() n FROM {DATABASE}.{SOURCE} FINAL')[0]['n']
            save(root/'verification.json',proof)
            if a.mode=='cutover':
                guard()
                active=c.query(f"SELECT query_id FROM system.processes WHERE startsWith(upper(trimLeft(query)),'INSERT') AND position(query,{literal(DATABASE)})>0 AND position(query,{literal(SOURCE)})>0 LIMIT 1")
                if active:raise ValueError('SEC source insert is active; wait for its natural drain')
                c.query(f'EXCHANGE TABLES {DATABASE}.{SOURCE} AND {DATABASE}.{target}',read=False)
                if table_info(c.query,SOURCE)['uuid']!=proof['target_uuid'] or table_info(c.query,target)['uuid']!=request['source']['uuid']:
                    raise ValueError('Atomic source cutover identities differ; preserve both tables')
                status(qualification_status='Atomic cutover applied; verify legacy containment after in-flight inserts drain')
                post_cutover(c.query,target,columns)
                proof['legacy_table']=target;proof['completed_at']=datetime.now(timezone.utc).isoformat()
                proof['post_cutover_source_containment']=True
                save(root/'completion.json',proof)
            status(state='complete',active={},qualification_status='Verified corrected source ready; legacy and raw snapshot retained')
            print(json.dumps(proof))
            return 0
    except BaseException as error:
        if not owned:
            raise  # A rejected duplicate must not change the owner's status/STOP.
        if isinstance(error,InterruptedError):
            status(state='stopped',error=str(error))
            return 130
        state['counts']['failed']+=1
        status(state='failed',error=str(error)[:1600])
        (root/'STOP').write_text('Migration failed. Preserve tables and receipts; diagnose and drain before resume.\n',encoding='utf-8')
        raise
    finally:
        heartbeat_stop.set()
        if owned:heartbeat_thread.join(timeout=2)
        c.close()


if __name__=='__main__':raise SystemExit(main())
