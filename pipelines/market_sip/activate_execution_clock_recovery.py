"""UUID-journaled atomic cutover; keeps old physical tables as rollback copies.

Active logical names stay stable for both ingestion and historical readers.
Clock rows switch before coverage, so new days cannot certify prematurely.
"""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import argparse
import json

from pipelines.market_sip.publish_execution_clock_recovery import (CLOCK,COVERAGE,BASE_CLOCK,BASE_COVERAGE,
    UNITS,FIELDS,COV_FIELDS,LIMITS,checked_stage,clock_rows,coverage_row,scalar,placement,inherited_equal)
from pipelines.market_sip.execution_clock_recovery import digest
from scripts.repair_qmd_live_canonical_bars import load_dotenv,connection_from_env
from scripts.audit_structural_baseline import write
from src.runtime_paths import WORKSTATION_RUNTIME_ROOT


def exchange_needed(current,source,target):
    if current==target:return False
    if current==source:return True
    raise ValueError('Unexpected active table UUID')


def inspect(client):
    names=','.join(f"'{n}'" for n in (CLOCK,COVERAGE,BASE_CLOCK,BASE_COVERAGE))
    return {r['name']:r for r in client.json_rows(f"SELECT name,toString(uuid) AS uuid,storage_policy FROM system.tables WHERE database='q_live' AND name IN ({names}) FORMAT JSONEachRow",timeout=30)}


def quiet(client):
    writers=client.json_rows("SELECT query_id FROM system.processes WHERE query_id!=currentQueryID() AND match(query,'(?is)(INSERT[[:space:]]+INTO|ALTER[[:space:]]+TABLE|CREATE[[:space:]]+TABLE|DROP[[:space:]]+TABLE|RENAME[[:space:]]+TABLE|EXCHANGE[[:space:]]+TABLES|TRUNCATE[[:space:]]+TABLE)') AND positionCaseInsensitive(query,'historical_event_execution_clock')>0 FORMAT JSONEachRow",timeout=30)
    mutations=client.json_rows("SELECT mutation_id FROM system.mutations WHERE database='q_live' AND position(table,'historical_event_execution_clock')=1 AND NOT is_done FORMAT JSONEachRow",timeout=30)
    if writers or mutations:raise ValueError('Clock writer/mutation active; retry after it completes')


def main(args):
    root=args.runtime.resolve()
    if not root.is_relative_to(WORKSTATION_RUNTIME_ROOT.resolve()) or not WORKSTATION_RUNTIME_ROOT.is_dir():
        raise ValueError('Required workstation runtime unavailable')
    publication=json.loads((root/'publication.json').read_text())
    if publication['phase']!='verified_successor_not_active':raise ValueError('Successor publication not verified')
    load_dotenv(Path(__file__).resolve().parents[2]/'.env');client=connection_from_env('q_live')
    if client.query("SELECT engine FROM system.databases WHERE name='q_live' FORMAT TSV",timeout=30).strip()!='Atomic':
        raise ValueError('Atomic database required')
    quiet(client);tables=inspect(client);path=root/'activation.json'
    if path.exists():
        state=json.loads(path.read_text())
        if state['publication_sha256']!=digest(publication):raise ValueError('Publication manifest changed')
    else:
        state=dict(version=1,publication_sha256=digest(publication),phase='prepared',tables={})
        for source,target in ((BASE_CLOCK,CLOCK),(BASE_COVERAGE,COVERAGE)):
            placement(client,target)
            state['tables'][source]=dict(source_uuid=tables[source]['uuid'],target_uuid=tables[target]['uuid'],replacement=target)
        write(path,state)
    for source,target,fields in ((BASE_CLOCK,CLOCK,FIELDS),(BASE_COVERAGE,COVERAGE,COV_FIELDS)):
        item=state['tables'][source];tables=inspect(client)
        switching=exchange_needed(tables[source]['uuid'],item['source_uuid'],item['target_uuid'])
        if switching and tables[target]['uuid']!=item['target_uuid']:raise ValueError('Successor identity changed')
        active=target if switching else source;backup=source if switching else target
        if tables[backup]['uuid']!=item['source_uuid']:raise ValueError('Original physical table lost')
        placement(client,active);inherited_equal(client,backup,active,fields)
        original=publication['source_counts'][source]
        added=publication['added_trade_rows'] if source==BASE_CLOCK else publication['added_certificates']
        if scalar(client,f'SELECT count() FROM q_live.{backup} FINAL')!=original:
            raise ValueError('Original changed since publication')
        if scalar(client,f'SELECT count() FROM q_live.{active} FINAL')!=original+added:
            raise ValueError('Successor count changed')
        for ticker,day in UNITS:
            _,snapshot,staged=checked_stage(root/f'{ticker}-{day}')
            proof=publication['inputs'][f'{ticker}-{day}']
            if digest(snapshot)!=proof['canonical'] or digest(staged)!=proof['matched']:
                raise ValueError('Input changed since publication')
            expected=clock_rows(ticker,day,staged) if source==BASE_CLOCK else [coverage_row(ticker,day,snapshot,staged)]
            order=' ORDER BY ordinal' if source==BASE_CLOCK else ''
            actual=client.json_rows(f"SELECT {fields} FROM q_live.{active} FINAL WHERE ticker='{ticker}' AND source_date='{day}'"+order+LIMITS+' FORMAT JSONEachRow',timeout=130)
            if actual!=expected:raise ValueError('Successor values changed')
        if switching:
            quiet(client)
            print(f'Atomically activating {source}',flush=True)
            client.query(f'EXCHANGE TABLES q_live.{source} AND q_live.{target}',timeout=30)
        tables=inspect(client)
        if tables[source]['uuid']!=item['target_uuid'] or tables[target]['uuid']!=item['source_uuid']:
            raise ValueError('Post-exchange UUID mismatch')
        quiet(client);inherited_equal(client,target,source,fields);placement(client,source)
        item['exchanged']=True;write(path,state)
    state['phase']='active_originals_retained';write(path,state)
    print('Active names now resolve to verified SSD successors. Originals retained under replacement names.',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime',type=Path,required=True)
    main(p.parse_args())
