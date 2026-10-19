"""Restart-safe physical reorder of normalized snapshots; preserve every logical row."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse,json,re
from contextlib import closing
from datetime import datetime,timezone
import psutil
from scripts import build_market_day as builder
from scripts.backfill_structural_sessions import save_json
from pipelines.market_sip.events import structural_checkpoint_storage as storage


def reordered_ddl(ddl,target,staging,key):
    old=storage.KEYS[target]+', checkpoint_boundary'
    if sorted(old.split(', '))!=sorted(key.split(', ')):
        raise ValueError('Sorting-key membership must not change')
    result=ddl.replace('CREATE TABLE '+target,'CREATE TABLE IF NOT EXISTS '+staging,1)
    result,n=re.subn(r'PRIMARY KEY \([^)]*\)','PRIMARY KEY (ticker, valid_from, checkpoint_boundary)',result)
    if n!=1:raise ValueError('Expected explicit existing primary key')
    result,n=re.subn(r'ORDER BY \([^)]*\)','ORDER BY ('+key+')',result)
    if n!=1 or result==ddl:raise ValueError('Unexpected source layout')
    return result


def fingerprint(client,table,partition):
    if not str(partition).isdigit() or not 0<=int(partition)<64:raise ValueError('Invalid normalized partition')
    limbs=','.join(f'groupBitXor(reinterpretAsUInt64(substring(h,{1+8*i},8))) AS sha_xor_{i}' for i in range(4))
    return client.query(f'SELECT count() AS rows,sumWithOverflow(fast) AS sum_hash,groupBitXor(fast) AS xor_hash,{limbs} '
        f"FROM (SELECT cityHash64(*) AS fast,SHA256(formatRowNoNewline('RowBinary',*)) AS h "
        f"FROM {table} FINAL WHERE _partition_id='{partition}')")[0]


def placement(client,table):
    name=table.split('.')[1]
    meta=client.query("SELECT uuid,storage_policy,sorting_key,primary_key,create_table_query FROM system.tables "
                      f"WHERE database='arte' AND name='{name}'")
    parts=client.query("SELECT DISTINCT disk_name FROM system.parts WHERE database='arte' "
                       f"AND table='{name}' AND active")
    if len(meta)!=1 or meta[0]['storage_policy']!=storage.POLICY or any(p['disk_name']!='live_market_ssd' for p in parts):
        raise ValueError('Absent or misplaced snapshot table')
    return meta[0]


def partitions(client,table):
    return [r['partition'] for r in client.query("SELECT DISTINCT partition FROM system.parts WHERE active "
        f"AND database='arte' AND table='{table.split('.')[1]}' ORDER BY partition")]


def run(a):
    if not a.run_dir.resolve().is_relative_to(Path('D:/TradingML/runtimes').resolve()):raise ValueError('Required runtime root')
    if not (a.run_dir/'STOP').exists():raise ValueError('Campaign STOP is required')
    if a.apply and os.environ.get('COMPUTERNAME','').upper()!='DESKTOP-SAAI85T':raise ValueError('Workstation only')
    for path in (a.run_dir/'workers').glob('lease-*.json'):
        lease=json.loads(path.read_text())
        try:
            if psutil.Process(lease['pid']).create_time()==lease['created']:raise ValueError('Prior worker is alive')
        except psutil.NoSuchProcess:pass
    os.environ['QMD_CLICKHOUSE_URL']=a.endpoint
    options=builder.parse_args(['--date','2026-01-02','--workers','1','--max-threads','1','--max-memory-gb','2'])
    root=a.run_dir/'normalized-snapshot-layout';root.mkdir(exist_ok=True)
    def state(**values):
        save_json(root/'status.json',dict(status='running',utc=datetime.now(timezone.utc).isoformat(),**values))
    with builder.build_lock(builder.RUNTIME/'structural-session-arte.lock'),builder.build_lock(root/'owner.lock'),closing(builder.Client(options,readonly=not a.apply)) as client:
        storage.require_schema(client)
        if client.query("SELECT engine FROM system.databases WHERE name='arte'")!=[dict(engine='Atomic')]:raise ValueError('Atomic database required')
        free=client.query("SELECT free_space FROM system.disks WHERE name='live_market_ssd'")
        if len(free)!=1 or int(free[0]['free_space'])<800*1024**3:raise ValueError('SSD reserve unavailable')
        for target,key in storage.SNAPSHOT_KEYS.items():
            meta=placement(client,target);staging=target+'_snapshot_layout_v1'
            receipt_dir=root/target.split('.')[1];receipt_dir.mkdir(exist_ok=True)
            request_path=receipt_dir/'request.json'
            exchanged=meta['sorting_key']==key
            if not a.apply:
                print(json.dumps(dict(table=target,partitions=len(partitions(client,target)),exchanged=exchanged)),flush=True);continue
            if request_path.exists():
                request=json.loads(request_path.read_text())
            else:
                if exchanged:raise ValueError('Missing migration request for reordered table')
                request=dict(table=target,source_uuid=meta['uuid'],key=key,partitions=partitions(client,target))
                save_json(request_path,request)
            if not exchanged:
                if meta['uuid']!=request['source_uuid']:raise ValueError('Source table ownership changed')
                client.http.execute(reordered_ddl(meta['create_table_query'],target,staging,key))
                staged=placement(client,staging)
                if staged['sorting_key']!=key or staged['primary_key']!='ticker, valid_from, checkpoint_boundary':raise ValueError('Wrong staging key')
                for index,part in enumerate(request['partitions']):
                    path=receipt_dir/(part+'.json')
                    state(table=target,stage='verifying/copying partition',partition=part,verified_partitions=index,total_partitions=len(request['partitions']))
                    if path.exists():
                        saved=json.loads(path.read_text())['fingerprint']
                        if fingerprint(client,target,part)!=saved or fingerprint(client,staging,part)!=saved:raise ValueError('Verified partition changed')
                        continue
                    before=fingerprint(client,target,part)
                    client.http.execute(f"ALTER TABLE {staging} DROP PARTITION '{part}'")
                    client.http.execute(f"INSERT INTO {staging} SELECT * FROM {target} FINAL WHERE _partition_id='{part}' "
                                        'SETTINGS max_bytes_before_external_sort=268435456')
                    placement(client,staging)
                    if fingerprint(client,staging,part)!=before or fingerprint(client,target,part)!=before:raise ValueError('Partition fingerprint differs')
                    save_json(path,dict(fingerprint=before,version='sha256-rowbinary-multiset-v1'))
                    print(json.dumps(dict(table=target,partition=part,status='verified',rows=before['rows'])),flush=True)
                if partitions(client,staging)!=request['partitions'] or partitions(client,target)!=request['partitions']:raise ValueError('Partition set changed')
                client.http.execute(f'EXCHANGE TABLES {target} AND {staging}')
            if placement(client,staging)['uuid']!=request['source_uuid']:raise ValueError('Original rollback table is not preserved')
            for index,part in enumerate(request['partitions']):
                state(table=target,stage='verifying exchanged table',partition=part,verified_partitions=index,total_partitions=len(request['partitions']))
                if fingerprint(client,target,part)!=json.loads((receipt_dir/(part+'.json')).read_text())['fingerprint']:raise ValueError('Exchanged table differs')
            save_json(receipt_dir/'complete.json',dict(status='verified',rollback_table=staging,partitions=len(request['partitions'])))
        if a.apply:
            storage.require_snapshot_layout(client)
            save_json(root/'status.json',dict(status='complete',utc=datetime.now(timezone.utc).isoformat()))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--endpoint',required=True)
    p.add_argument('--apply',action='store_true')
    args=p.parse_args()
    try:run(args)
    except BaseException as error:
        if (args.run_dir/'normalized-snapshot-layout').is_dir():
            save_json(args.run_dir/'normalized-snapshot-layout'/'status.json',
                dict(status='failed',error=type(error).__name__+': '+str(error)[:500]))
        raise
