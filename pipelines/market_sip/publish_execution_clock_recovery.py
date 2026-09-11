"""Restart-safe successor publication; does not switch readers or writers."""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import argparse
import json
from hashlib import sha256

from pipelines.market_sip.execution_clock_recovery import digest,reconcile,VERSION
from scripts.repair_qmd_live_canonical_bars import load_dotenv,connection_from_env
from scripts.audit_structural_baseline import write
from src.runtime_paths import WORKSTATION_RUNTIME_ROOT

CLOCK='historical_event_execution_clock_rest_v2'
COVERAGE='historical_event_execution_clock_coverage_rest_v2'
BASE_CLOCK='historical_event_execution_clock_v1'
BASE_COVERAGE='historical_event_execution_clock_coverage_v1'
UNITS=[(t,d) for d in ('2026-08-13','2026-08-24','2026-08-25') for t in ('SUGP','JUNS')]
CONTROLS=[('JUNS','2026-08-19'),('SUGP','2026-08-19'),('SUGP','2026-08-20')]
FIELDS='source_date,ticker,ordinal,sip_timestamp_us,execution_timestamp_us,build_step'
COV_FIELDS='source_date,ticker,event_count,trade_count,clock_count,delayed_trade_report_count,first_ordinal,next_ordinal,build_step,source_filter_key'
LIMITS=' SETTINGS max_threads=2,max_memory_usage=1073741824,max_execution_time=120'


def checked_stage(folder):
    plan=json.loads((folder/'plan.json').read_text())
    report=json.loads((folder/'validation.json').read_text())
    snapshot=json.loads((folder/'canonical-snapshot.json').read_text())
    staged=json.loads((folder/'matched-staging.json').read_text())
    if plan['version']!=VERSION or not report['complete']:
        raise ValueError('Unsupported or failed staging validation')
    if digest(snapshot)!=report['canonical_sha256'] or digest(staged)!=report['matched_sha256']:
        raise ValueError('Staging hash mismatch')
    expected_filter='flatfile_direct_events_v1|drop_trade_correction_codes=07,08,10,11|condition_slots=5'
    if snapshot.get('provenance')!=[{'source_filter_key':expected_filter}]:
        raise ValueError('Unsupported canonical ordering/filter provenance')
    vendor=[];hashes=[];next_url=None
    for index,path in enumerate(sorted(folder.glob('vendor-page-*.json'))):
        page=json.loads(path.read_text())
        if path.name!=f'vendor-page-{index:04}.json' or (index and page['request_url']!=next_url):
            raise ValueError('Source pagination chain mismatch')
        if digest(page['results'])!=page['results_sha256']:raise ValueError('Vendor page hash mismatch')
        hashes.append(page['results_sha256']);vendor.extend(page['results']);next_url=page['next_url']
    if not hashes or next_url or hashes!=report['vendor_page_sha256']:
        raise ValueError('Incomplete source pages')
    tokens={int(r['modifier_int']):int(r['token_id']) for r in snapshot['reference']}
    reproduced,proof=reconcile(snapshot['rows'],vendor,tokens,verified_order_contract=True)
    if not proof['complete'] or reproduced!=staged:raise ValueError('Staged recovery did not reproduce')
    return plan,snapshot,staged


def clock_rows(ticker,day,staged):
    return [dict(source_date=day,ticker=ticker,**r,build_step=20260911) for r in staged]


def coverage_row(ticker,day,snapshot,staged):
    bounds=snapshot['bounds']
    return dict(source_date=day,ticker=ticker,event_count=int(bounds['events']),trade_count=len(staged),
        clock_count=len(staged),delayed_trade_report_count=sum(r['execution_timestamp_us']//1000000<r['sip_timestamp_us']//1000000 for r in staged),
        first_ordinal=int(bounds['begin']),next_ordinal=int(bounds['end']),build_step=20260911,
        source_filter_key='rest_clock_recovery_v2|flatfile_direct_events_v1|drop_trade_correction_codes=07,08,10,11|condition_slots=5|execution_clock_v1|delayed_audit_v1')


def scalar(client,sql):
    return int(client.query(sql+LIMITS+' FORMAT TSV',timeout=130).strip())


def placement(client,table):
    meta=client.json_rows(f"SELECT storage_policy FROM system.tables WHERE database='q_live' AND name='{table}' FORMAT JSONEachRow",timeout=30)
    if len(meta)!=1 or meta[0]['storage_policy']!='live_market_ssd':raise ValueError('Incorrect successor storage policy')
    parts=client.json_rows(f"SELECT DISTINCT disk_name FROM system.parts WHERE active AND database='q_live' AND table='{table}' FORMAT JSONEachRow",timeout=30)
    if any(p['disk_name']!='live_market_ssd' for p in parts):raise ValueError('Incorrect successor physical part placement')


def inherited_equal(client,source,target,fields):
    sql=f'SELECT count() FROM (SELECT {fields} FROM q_live.{source} FINAL EXCEPT DISTINCT SELECT {fields} FROM q_live.{target} FINAL)'
    if scalar(client,sql):raise ValueError(f'Inherited values differ: {source}')


def main(args):
    root=args.runtime.resolve()
    if not WORKSTATION_RUNTIME_ROOT.is_dir() or not root.is_relative_to(WORKSTATION_RUNTIME_ROOT.resolve()):
        raise ValueError('Publisher requires workstation runtime')
    load_dotenv(Path(__file__).resolve().parents[2]/'.env');client=connection_from_env('q_live')
    inputs={};stages={}
    print('Preflight: revalidating source snapshots and controls',flush=True)
    for ticker,day in UNITS+CONTROLS:
        folder=root/f'{ticker}-{day}';plan,snapshot,staged=checked_stage(folder)
        if plan['ticker']!=ticker or plan['day']!=day:raise ValueError('Staging identity mismatch')
        bound=snapshot['bounds']
        current=client.json_rows(f"SELECT * FROM market_sip_compact.events_2026 PREWHERE ticker='{ticker}' WHERE ordinal>={bound['begin']} AND ordinal<{bound['end']} AND bitAnd(event_meta,1)=1 ORDER BY ordinal"+LIMITS+' FORMAT JSONEachRow',timeout=130)
        if current!=snapshot['rows']:raise ValueError('Live canonical rows differ from verified snapshot')
        live_bound=client.json_rows(f"SELECT argMax(event_count,tuple(build_step,updated_at)) AS events,argMax(next_ordinal-event_count,tuple(build_step,updated_at)) AS begin,argMax(next_ordinal,tuple(build_step,updated_at)) AS end FROM market_sip_compact.events_ordinal_continuity WHERE ticker='{ticker}' AND source_date='{day}' FORMAT JSONEachRow",timeout=30)[0]
        if live_bound!=bound:raise ValueError('Canonical ordinal bounds changed')
        if (ticker,day) in CONTROLS:
            control=client.json_rows(f"SELECT ordinal,sip_timestamp_us,execution_timestamp_us FROM q_live.{BASE_CLOCK} FINAL WHERE ticker='{ticker}' AND source_date='{day}' ORDER BY ordinal"+LIMITS+' FORMAT JSONEachRow',timeout=130)
            if control!=staged:raise ValueError('Control sidecar mismatch')
        inputs[f'{ticker}-{day}']=dict(canonical=digest(snapshot),matched=digest(staged))
        stages[(ticker,day)]=(snapshot,staged)
    source_counts={table:scalar(client,f'SELECT count() FROM q_live.{table} FINAL') for table in (BASE_CLOCK,BASE_COVERAGE)}
    manifest=dict(version=1,source_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),inputs=inputs,
                  source_counts=source_counts,targets=[CLOCK,COVERAGE],phase='planned')
    state_path=root/'publication.json'
    if state_path.exists():
        old=json.loads(state_path.read_text())
        if any(old[k]!=manifest[k] for k in ('version','source_sha256','inputs','source_counts','targets')):
            raise ValueError('Frozen publication manifest/source changed')
    else:
        existing=scalar(client,f"SELECT count() FROM system.tables WHERE database='q_live' AND name IN ('{CLOCK}','{COVERAGE}')")
        if existing:raise ValueError('Unowned successor table exists')
        write(state_path,manifest)
    policy=client.json_rows("SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd' FORMAT JSONEachRow",timeout=30)
    if not policy or any(r['disks']!=['live_market_ssd'] for r in policy):raise ValueError('Required SSD policy unavailable')
    for source,target,order in [(BASE_CLOCK,CLOCK,'ticker,ordinal'),(BASE_COVERAGE,COVERAGE,'source_date,ticker')]:
        client.query(f"CREATE TABLE IF NOT EXISTS q_live.{target} AS q_live.{source} ENGINE=ReplacingMergeTree(updated_at) PARTITION BY toYYYYMM(source_date) ORDER BY ({order}) SETTINGS storage_policy='live_market_ssd'",timeout=30)
        placement(client,target)
        print(f'Copying certified source: {source}',flush=True)
        client.query(f'INSERT INTO q_live.{target} SELECT * FROM q_live.{source} FINAL WHERE ({order}) NOT IN (SELECT {order} FROM q_live.{target} FINAL)'+LIMITS,timeout=130)
        inherited_equal(client,source,target,FIELDS if target==CLOCK else COV_FIELDS)
    for ticker,day in UNITS:
        snapshot,staged=stages[(ticker,day)]
        expected=clock_rows(ticker,day,staged)
        actual=client.json_rows(f"SELECT {FIELDS} FROM q_live.{CLOCK} FINAL WHERE ticker='{ticker}' AND source_date='{day}' ORDER BY ordinal"+LIMITS+' FORMAT JSONEachRow',timeout=130)
        by_ordinal={r['ordinal']:r for r in expected}
        if any(by_ordinal.get(r['ordinal'])!=r for r in actual):raise ValueError('Existing recovered rows differ')
        present={r['ordinal'] for r in actual};missing=[r for r in expected if r['ordinal'] not in present]
        for offset in range(0,len(missing),5000):
            body='\n'.join(json.dumps(r,separators=(',',':')) for r in missing[offset:offset+5000])
            client.query(f'INSERT INTO q_live.{CLOCK} ({FIELDS}) FORMAT JSONEachRow\n'+body,timeout=60)
        actual=client.json_rows(f"SELECT {FIELDS} FROM q_live.{CLOCK} FINAL WHERE ticker='{ticker}' AND source_date='{day}' ORDER BY ordinal"+LIMITS+' FORMAT JSONEachRow',timeout=130)
        if actual!=expected:raise ValueError('Published recovered values differ')
        certificate=coverage_row(ticker,day,snapshot,staged)
        old=client.json_rows(f"SELECT {COV_FIELDS} FROM q_live.{COVERAGE} FINAL WHERE ticker='{ticker}' AND source_date='{day}' FORMAT JSONEachRow",timeout=30)
        if old and old!=[certificate]:raise ValueError('Existing certificate differs')
        if not old:client.query(f'INSERT INTO q_live.{COVERAGE} ({COV_FIELDS}) FORMAT JSONEachRow\n'+json.dumps(certificate),timeout=30)
        verified=client.json_rows(f"SELECT {COV_FIELDS} FROM q_live.{COVERAGE} FINAL WHERE ticker='{ticker}' AND source_date='{day}' FORMAT JSONEachRow",timeout=30)
        if verified!=[certificate]:raise ValueError('Published certificate values differ')
        print(f'{ticker} {day}: {len(expected)} exact rows verified and certified in successor',flush=True)
    for table,target,fields in [(BASE_CLOCK,CLOCK,FIELDS),(BASE_COVERAGE,COVERAGE,COV_FIELDS)]:
        if scalar(client,f'SELECT count() FROM q_live.{table} FINAL')!=source_counts[table]:raise ValueError('Source changed during publication')
        inherited_equal(client,table,target,fields);placement(client,target)
        expected=source_counts[table]+(sum(len(stages[u][1]) for u in UNITS) if target==CLOCK else len(UNITS))
        if scalar(client,f'SELECT count() FROM q_live.{target} FINAL')!=expected:raise ValueError('Successor total differs')
    manifest.update(phase='verified_successor_not_active',added_trade_rows=sum(len(stages[u][1]) for u in UNITS),added_certificates=len(UNITS))
    write(state_path,manifest)
    print('Successor publication verified. Existing readers and writers are unchanged.',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime',type=Path,required=True)
    main(p.parse_args())
