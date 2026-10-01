"""SELECT-only sparse bps feature/score preparation. Never starts training."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,hashlib
from contextlib import closing
from pathlib import Path
from datetime import date
import polars as pl
from research.mlops.clickhouse import discover_clickhouse_env_files
from research.mlops.env import load_env_files
from research.rl_trading.v1 import arte_source
from research.rl_trading.v1.common import file_hash,bounds
from research.rl_trading.v6.training_gate import require_dataset
from research.rl_trading.v6.session_data import open_session
from research.rl_trading.v6.environment_source import ArteExecutionSource
from research.rl_trading.v6.execution_sidecar import build_cost_sidecar
from research.rl_trading.v6.feature_normalization import fit_normalization


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('dataset','episode-root','early-manifest','late-manifest','ledger','luld-root','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--runtime-root',type=Path,default=Path('D:/TradingML/runtimes'))
    p.add_argument('--days',nargs='+',required=True)
    p.add_argument('--max-candidates',type=int,help='Explicit bounded diagnostic, not training-ready')
    p.add_argument('--fit-normalization',action='store_true',help='Stream all16 certified training banks')
    p.add_argument('--resume',action='store_true',help='Verify and reuse complete days; incomplete days fail closed')
    args=p.parse_args();runtime=args.runtime_root.resolve()
    for path in (args.dataset,args.episode_root,args.early_manifest,args.late_manifest,args.ledger,args.luld_root,args.output):
        if not path.resolve().is_relative_to(runtime):raise ValueError('Preparation path escaped runtime')
    if args.output.exists() and not args.resume:raise ValueError('Fresh output required or explicit --resume')
    if args.max_candidates is not None and args.max_candidates<1:raise ValueError('Positive diagnostic bound required')
    data=require_dataset(args.dataset,runtime_root=runtime)
    entries=[e for e in data['days'] if e['day'] in args.days]
    if len(entries)!=len(set(args.days)) or len(set(args.days))!=len(args.days):raise ValueError('Only unique audited days')
    args.output.mkdir(parents=True,exist_ok=args.resume)
    if args.fit_normalization:
        def sessions():
            for e in data['days']:
                if e['role']=='train':
                    yield open_session(Path(e['bank_root']),runtime_root=runtime,previous_root=Path(e['previous_root']))
        proof=fit_normalization(sessions(),dataset_sha256=file_hash(args.dataset))
        (args.output/'normalization.json').write_text(json.dumps(proof,sort_keys=True))
        print('all16 training-only normalization complete',flush=True)
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    for e in entries:
        session=open_session(Path(e['bank_root']),runtime_root=runtime,previous_root=Path(e['previous_root']))
        root=Path(e['bank_root']);cert=json.loads((root/'complete.json').read_text())
        if file_hash(root/'candidates.parquet')!=cert['outputs']['candidates']['sha256']:raise ValueError('Candidate source changed')
        labels=args.episode_root/e['day'];proof=json.loads((labels/'complete.json').read_text())
        if proof['bank_certificate_sha256']!=session.source_certificate_sha256:raise ValueError('Episode bank binding changed')
        if file_hash(labels/'flat.parquet')!=proof['files']['flat']['sha256']:raise ValueError('Opportunity labels changed')
        flat=pl.read_parquet(labels/'flat.parquet')
        original=pl.read_parquet(root/'candidates.parquet').filter(pl.col('direction')==1)
        candidates=original.join(flat.filter(pl.col('enter_probability')>0).select('episode_uid','time_us'),
            on=['episode_uid','time_us'],how='inner',validate='1:1')
        if args.max_candidates:candidates=candidates.sort('time_us','episode_uid').head(args.max_candidates)
        mapping=original.select('listing_id','ticker').unique()
        requests=flat.join(mapping,on='listing_id',validate='m:1').select('ticker','time_us',pl.col('close').alias('reference')).unique()
        if args.max_candidates:
            requested_episodes=candidates.select('episode_uid').unique()
            requests=flat.join(requested_episodes,on='episode_uid').join(mapping,on='listing_id',validate='m:1').select(
                'ticker','time_us',pl.col('close').alias('reference')).unique()
        done=args.output/e['day']/'complete.json'
        if args.resume and done.exists():
            saved=json.loads(done.read_text())
            plan=json.loads((root/'plan.json').read_text())
            expected_scope='bounded_diagnostic' if args.max_candidates else 'complete_day'
            if (saved.get('sparse_coverage_version')!='rl-v6-certified-event-sparse-liquidity-v1' or
                saved.get('bank_certificate_sha256')!=session.source_certificate_sha256 or
                saved.get('input_candidate_sha256')!=hashlib.sha256(candidates.serialize()).hexdigest() or
                saved.get('preparation_scope')!=expected_scope or saved.get('day')!=e['day'] or
                saved.get('build_id')!=plan['source_build_id'] or
                saved.get('luld_certificate')!=file_hash(args.luld_root/e['day']/'complete.json') or
                any(file_hash(done.parent/(name+'.parquet'))!=info['sha256'] for name,info in saved['files'].items())):
                raise ValueError('Completed execution-feature day changed; cannot resume')
            print(json.dumps({'day':e['day'],'status':'verified_reused_complete'}),flush=True)
            continue
        with closing(arte_source.reader(threads=2)) as reader:
            source=arte_source.load_build(args.early_manifest if session.day<=date(2026,8,17) else args.late_manifest,args.ledger,[session.day])
            plan=json.loads((root/'plan.json').read_text())
            if source['build_id']!=plan['source_build_id'] or source['definition_hash']!=plan['source_definition_hash']:raise ValueError('Execution source changed')
            provider=ArteExecutionSource(reader,source,args.ledger,session.day,end_us=bounds(session.day)[1])
            from research.rl_trading.v6.build_luld import open_sidecar
            provider.luld,provider.luld_certificate=open_sidecar(args.luld_root,session.day,source)
            report=build_cost_sidecar(provider,candidates,requests,args.output/e['day'],
                bank_sha=session.source_certificate_sha256)
        report['preparation_scope']='bounded_diagnostic' if args.max_candidates else 'complete_day'
        (args.output/e['day']/'complete.json').write_text(json.dumps(report,sort_keys=True))
        print(json.dumps(report),flush=True)


if __name__=='__main__':main()
