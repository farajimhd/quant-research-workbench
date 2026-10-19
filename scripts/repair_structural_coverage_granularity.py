"""Repack structural coverage payloads without changing their logical contents.

Requires the structural campaign to be stopped and drained. Each partition is
independently fingerprinted, copied to SSD staging, checked, atomically replaced,
and checked again. Staging and durable receipts permit restart after interruption.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import json
import re
from datetime import datetime, timezone
from contextlib import closing
import psutil
from scripts import build_market_day as builder
from pipelines.market_sip.events import structural_checkpoint_storage as storage

TARGET = storage.COVERAGE_TABLE
STAGING = TARGET + '_granularity_repair_v1'


def fingerprint(client, table, partition):
    return client.query(f'SELECT count() AS rows, sum(cityHash64(*)) AS sum_hash, '
        f'groupBitXor(cityHash64(*)) AS xor_hash, '
        "countIf(streaming_state!='' AND lower(hex(SHA256(streaming_state)))!=streaming_state_hash) AS bad_payloads "
        f'FROM {table} FINAL WHERE toYYYYMM(session_date)={int(partition)}')[0]


def run(args):
    root = args.run_dir
    if not (root/'STOP').exists():
        raise ValueError('Campaign STOP is required')
    for path in (root/'workers').glob('lease-*.json'):
        lease = json.loads(path.read_text())
        try:
            if psutil.Process(lease['pid']).create_time() == lease['created']:
                raise ValueError('Prior structural worker is still alive')
        except psutil.NoSuchProcess:
            pass
    os.environ['QMD_CLICKHOUSE_URL'] = args.endpoint
    options = builder.parse_args(['--date','2026-01-02','--max-threads','1','--max-memory-gb','8'])
    with closing(builder.Client(options, readonly=not args.apply)) as client:
        storage.require_schema(client)
        ddl = client.query("SELECT create_table_query FROM system.tables WHERE database='arte' "
                           "AND name='structural_level_coverage_v7'")[0]['create_table_query']
        parts = client.query("SELECT DISTINCT partition FROM system.parts WHERE active AND database='arte' "
            "AND table='structural_level_coverage_v7' AND partition>='202601' ORDER BY partition")
        if not args.apply:
            print(json.dumps(dict(partitions=parts,query_memory_gib=8,threads=1,staging=STAGING)))
            return
        staging_ddl = ddl.replace('CREATE TABLE '+TARGET, 'CREATE TABLE IF NOT EXISTS '+STAGING, 1)
        staging_ddl = re.sub(r'index_granularity = \d+', 'index_granularity = 1', staging_ddl)
        if 'index_granularity = 1' not in staging_ddl:
            raise ValueError('Expected explicit granularity setting')
        client.http.execute(staging_ddl)
        info = client.query("SELECT storage_policy,create_table_query FROM system.tables WHERE database='arte' "
                            f"AND name='{STAGING.split('.')[1]}'")[0]
        if info['storage_policy'] != storage.POLICY or not re.search(r'index_granularity = 1(?:,|$)',info['create_table_query']):
            raise ValueError('Unexpected staging storage layout')
        client.http.execute(f'ALTER TABLE {TARGET} MODIFY SETTING index_granularity=1')
        receipts = root/'coverage-granularity-repair'
        receipts.mkdir(exist_ok=True)
        for part in parts:
            partition = part['partition']
            receipt = receipts/(partition+'.json')
            if receipt.exists():
                saved = json.loads(receipt.read_text())
                if fingerprint(client,TARGET,partition) != saved['fingerprint']:
                    raise ValueError('Completed migration partition changed')
                print(json.dumps(dict(partition=partition,status='reused')),flush=True)
                continue
            before = fingerprint(client,TARGET,partition)
            if int(before['bad_payloads']):
                raise ValueError('Source payload hash mismatch')
            client.http.execute(f"ALTER TABLE {STAGING} DROP PARTITION '{partition}'")
            client.http.execute(f'INSERT INTO {STAGING} SELECT * FROM {TARGET} FINAL '
                                f'WHERE toYYYYMM(session_date)={int(partition)}')
            copied = fingerprint(client,STAGING,partition)
            if copied != before:
                raise ValueError('Staging fingerprint differs')
            placement = client.query("SELECT DISTINCT disk_name FROM system.parts WHERE active AND database='arte' "
                f"AND table='{STAGING.split('.')[1]}' AND partition='{partition}'")
            if not placement or any(row['disk_name'] != 'live_market_ssd' for row in placement):
                raise ValueError('Staging parts are not on live_market_ssd')
            if fingerprint(client,TARGET,partition) != before:
                raise ValueError('Source changed while copying')
            client.http.execute(f"ALTER TABLE {TARGET} REPLACE PARTITION '{partition}' FROM {STAGING}")
            if fingerprint(client,TARGET,partition) != before:
                raise ValueError('Replacement fingerprint differs')
            storage.storage_preflight(client)
            receipt.write_text(json.dumps(dict(partition=partition,fingerprint=before,
                utc=datetime.now(timezone.utc).isoformat()),indent=2))
            print(json.dumps(dict(partition=partition,status='verified',fingerprint=before)),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--endpoint',required=True)
    parser.add_argument('--apply',action='store_true')
    run(parser.parse_args())
