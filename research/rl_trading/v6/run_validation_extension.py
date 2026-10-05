"""Prepare seven full-market sessions with a frozen random sealed split.

Reuses certified immutable bars and Aug25 context; never trains or publishes
sealed data to the Research registries. Resume the same output/commit only.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('POLARS_MAX_THREADS','1')
os.environ.setdefault('OMP_NUM_THREADS','1')
import argparse
from datetime import date
import json
from pathlib import Path
import subprocess
import sys
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from research.rl_trading.v1.common import digest,file_hash
from research.rl_trading.v6.opportunity_dataset import write_json
from research.rl_trading.v6.validation_split import read_split

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--split-manifest',type=Path,required=True)
    parser.add_argument('--source-commit',required=True)
    parser.add_argument('--workers',type=int,default=16)
    parser.add_argument('--preflight-only',action='store_true')
    args=parser.parse_args(argv)
    runtime=Path('D:/TradingML/runtimes').resolve();output=args.output.resolve()
    if not runtime.is_dir() or not output.is_relative_to(runtime) or not args.split_manifest.resolve().is_relative_to(output):
        raise ValueError('Explicit workstation runtime/split required')
    if len(args.source_commit)!=40 or any(c not in '0123456789abcdef' for c in args.source_commit) or not 1<=args.workers<=16:
        raise ValueError('Exact pushed source commit and bounded workers required')
    split=read_split(args.split_manifest)
    repair=runtime/'rl-v6-reporting-repair-20261002'
    manifest=repair/'bars/latest.json';ledger=repair/'build-ledger-v2.sqlite3'
    registries={name:file_hash(runtime/name) for name in ('rl-v6-active-labels.json','rl-v6-active-market-teacher.json')}
    active=json.loads((runtime/'rl-v6-active-labels.json').read_text());original_path=Path(active['dataset'])
    if file_hash(original_path)!=active['sha256']:raise ValueError('Original publication changed')
    original=json.loads(original_path.read_text())
    context=next(e for e in original['days'] if e['day']==split['context_day'])
    binding=dict(source_commit=args.source_commit,split_sha256=file_hash(args.split_manifest),source_dataset_sha256=active['sha256'],bar_manifest_sha256=file_hash(manifest),registries=registries)
    output.mkdir(parents=True,exist_ok=True)
    pin=output/'manifest.json'
    if pin.exists() and json.loads(pin.read_text())['binding']!=binding:raise ValueError('Campaign binding changed')
    write_json(pin,dict(version='rl-v6-validation-extension-campaign-v1',binding=binding,args={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},training_started=False))
    os.environ['QW_RUNTIME_ROOT']=str(runtime)
    os.environ['QMD_CLICKHOUSE_URL']='http://127.0.0.1:8123'
    os.environ['REAL_LIVE_CLICKHOUSE_WRITE_URL']='http://127.0.0.1:8123'
    from scripts.backfill_trade_reporting_flags import exclusive
    with exclusive(output/'writer'):
        stage='preflight'
        def progress(status,**extra):write_json(output/'progress.json',dict(status=status,stage=stage,**extra))
        try:
            progress('running')
            from research.mlops.clickhouse import discover_clickhouse_env_files
            from research.mlops.env import load_env_files
            from research.rl_trading.v1 import arte_source,reference_features
            from research.rl_trading.v6.source import require_reporting_coverage
            from research.rl_trading.v6.universe_scope import scope_population
            load_env_files(discover_clickhouse_env_files(),verbose=False)
            source=arte_source.load_build(manifest,ledger,[date.fromisoformat(d) for d in [split['context_day']]+split['days']])
            client=arte_source.reader(threads=1)
            try:
                storage=arte_source.storage_check(client);reference_features.storage_check(client)
                receipts=[]
                for label in [split['context_day']]+split['days']:
                    day=date.fromisoformat(label);require_reporting_coverage(source,day)
                    population,proof=arte_source.population(client,source,day)
                    selected,scope=scope_population(client,population)
                    if not selected:raise ValueError('Empty scoped population')
                    receipts.append(dict(day=label,units_sha256=digest(source['units'][label]),population_sha256=proof['snapshot_hash'],scope=scope))
            finally:client.close()
            write_json(output/'preflight.json',dict(status='passed',binding=binding,build_id=source['build_id'],storage=storage,days=receipts))
            if args.preflight_only:progress('preflight_complete');return 0
            repo=Path(__file__).resolve().parents[3]
            def run(name,arguments):
                nonlocal stage
                stage=name;progress('running')
                with (output/(name+'.log')).open('a',encoding='utf-8') as log:
                    subprocess.run([sys.executable,'-B',*arguments],cwd=repo,env=dict(os.environ),stdout=log,stderr=subprocess.STDOUT,check=True)
            roots={split['context_day']:context['bank_root']};previous=split['context_day']
            for number,day in enumerate(split['days']):
                bank=output/'banks'/day
                run('features-'+day,['-m','research.rl_trading.v6.build','--manifest',str(manifest),'--ledger',str(ledger),
                    '--date',day,'--previous-date',previous,'--workers',str(args.workers),'--output',str(bank),'--split-manifest',str(args.split_manifest)])
                if not (bank/'complete.json').exists():raise ValueError('Feature day did not certify')
                roots[day]=str(bank);previous=day
                progress('running',completed_sessions=number+1,total_sessions=7)
            write_json(output/'day-roots.json',dict(version='rl-trading-v6-forward-candle-day-roots',day_roots=roots))
            run('labels-1a',['research/rl_trading/v6/run_prepare_labels.py','--source-manifest',str(output/'day-roots.json'),
                '--bar-manifest',str(manifest),'--bar-ledger',str(ledger),'--output',str(output/'labels-1a'),'--workers','8',
                '--listings-per-shard','32','--source-commit',args.source_commit,'--split-manifest',str(args.split_manifest),'--context-dataset',str(original_path)])
            run('labels-1b',['research/rl_trading/v6/run_prepare_market_teacher.py','--source-dataset',str(output/'labels-1a/dataset.json'),
                '--source-commit',args.source_commit,'--output',str(output/'labels-1b'),'--validation-extension'])
            if any(file_hash(runtime/name)!=sha for name,sha in registries.items()):raise ValueError('Public registries changed')
            one=json.loads((output/'labels-1a/dataset.json').read_text());two=json.loads((output/'labels-1b/dataset.json').read_text())
            write_json(output/'complete.json',dict(status='complete',binding=binding,roles=split['roles'],sessions=7,
                dataset_1a_sha256=file_hash(output/'labels-1a/dataset.json'),dataset_1b_sha256=file_hash(output/'labels-1b/dataset.json'),
                rows=sum(e['valid_rows'] for e in one['days']),shards=sum(len(json.loads((Path(e['teacher_root'])/'complete.json').read_text())['shards']) for e in one['days']),
                registries_preserved=True,training_started=False,sealed_labels_exposed=False))
            stage='complete';progress('complete',completed_sessions=7,total_sessions=7)
        except Exception as error:
            progress('failed',reason=str(error));raise
    return 0

if __name__=='__main__':raise SystemExit(main())
