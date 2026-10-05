"""Immutable phase-1b copies of all approved 1a shards; no training invocation."""
import argparse
import json
from pathlib import Path
import shutil
import numpy as np
import polars as pl
from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6 import opportunity_dataset as source
from research.rl_trading.v6.market_teacher_preview import Config, select, group, VERSION as GROUPING

VERSION = 'rl-v6-market-teacher-dataset-v1'
ACTION_ORDER = ['ENTRY','WAIT','HOLD','EXIT']

def copy_labels(frame, decisions):
    """Keep every original field/row, exposing explicit new targets and originals."""
    keys=['listing_id','pair_id']
    joined=frame.join(decisions.select(*keys,pl.col('selected').alias('episode_selected'),
        'group_id','allocation_ratio','active_score_sum','active_count'),on=keys,how='left',validate='m:1')
    if joined.filter((pl.col('pair_id')>0)&pl.col('episode_selected').is_null()).height:
        raise ValueError('Paired candle has no selection decision')
    rejected=pl.col('episode_selected').eq(False).fill_null(False)
    joined=joined.with_columns(pl.col('action').alias('action_1a'),pl.col('label_value').alias('label_value_1a'),
        pl.col('reference_action').alias('reference_action_1a'),
        pl.when(rejected).then(pl.lit('WAIT')).otherwise(pl.col('action')).alias('action'),
        pl.when(rejected).then(pl.lit('WAIT')).otherwise(pl.col('reference_action')).alias('reference_action'),
        pl.when(rejected).then(pl.lit(1.)).otherwise(pl.col('label_value')).alias('label_value'))
    entry=pl.col('action').eq('ENTRY') & pl.col('episode_selected').fill_null(False)
    joined=joined.with_columns(entry.alias('allocation_loss_mask'),
        pl.when(entry).then(pl.col('allocation_ratio')).otherwise(pl.lit(0.)).alias('allocation_ratio'))
    q=pl.col('label_value');action=pl.col('action')
    return joined.with_columns(pl.concat_list(
        pl.when(action=='ENTRY').then(q).otherwise(0.),
        pl.when(action=='WAIT').then(1.).when(action=='ENTRY').then(1.-q).otherwise(0.),
        pl.when(action=='HOLD').then(q).when(action=='EXIT').then(1.-q).otherwise(0.),
        pl.when(action=='EXIT').then(q).when(action=='HOLD').then(1.-q).otherwise(0.)
    ).alias('teacher_probabilities'))

def read_targets(root):
    """Canonical bounded reader; consumers must not reconstruct actions from gains."""
    root=Path(root);receipt=json.loads((root/'complete.json').read_text())
    if receipt['version']!=VERSION:raise ValueError('Wrong 1b shard contract')
    path=root/'labels.parquet'
    if file_hash(path)!=receipt['files']['labels']['sha256']:raise ValueError('1b target bytes changed')
    return pl.read_parquet(path)

def audit_dataset(dataset, original, progress):
    """Independent read-back of every shard plus timestamp-event sizing audit."""
    if len(dataset['days'])!=19 or [e['day'] for e in dataset['days']] != [e['day'] for e in [original['context']]+original['days']]:
        raise ValueError('Incomplete approved 1b population')
    totals=dict(rows=0,suppressed_rows=0,selected=0,groups=0,shards=0)
    for entry,prior in zip(dataset['days'],[original['context']]+original['days']):
        root=Path(entry['root']);proof=json.loads((root/'complete.json').read_text())
        if file_hash(root/'complete.json')!=entry['sha256'] or file_hash(Path(prior['teacher_root'])/'complete.json')!=prior['teacher_sha256']:
            raise ValueError('Day certificate changed')
        if file_hash(root/'decisions.parquet')!=proof['decisions_sha256']:raise ValueError('Decisions changed')
        decisions=pl.read_parquet(root/'decisions.parquet');selected=decisions.filter(pl.col('selected'))
        # Independent endpoint-event sums; simultaneous starts are atomic and ends exclusive.
        if selected.height:
            starts=selected['time_us'].to_numpy();ends=selected['entry_target_us'].to_numpy();scores=selected['selection_score'].to_numpy()
            def cumulative(times):
                order=np.argsort(times,kind='stable');ordered=times[order]
                sums=np.concatenate(([0.],np.cumsum(scores[order])))
                indices=np.searchsorted(ordered,starts,side='right')
                return sums[indices],indices
            added,nstarts=cumulative(starts);removed,nends=cumulative(ends);denominator=added-removed
            if not np.allclose(denominator,selected['active_score_sum'].to_numpy(),rtol=1e-9,atol=1e-9) or not np.array_equal(nstarts-nends,selected['active_count'].to_numpy()):
                raise ValueError('Independent active sizing audit failed')
            if not np.allclose(scores/denominator,selected['allocation_ratio'].to_numpy(),rtol=1e-9,atol=1e-9):raise ValueError('Sizing ratios differ')
        rows=suppressed=0
        for number,item in enumerate(proof['shards']):
            progress('publication_audit',day=entry['day'],completed_shards=number,total_shards=len(proof['shards']))
            folder=root/item['path']
            if file_hash(folder/'complete.json')!=item['sha256']:raise ValueError('Shard receipt changed')
            receipt=json.loads((folder/'complete.json').read_text());copied=read_targets(folder)
            original_frame=pl.read_parquet(Path(prior['teacher_root'])/item['path']/'labels.parquet')
            if not copied.equals(copy_labels(original_frame,decisions)):raise ValueError('Copied labels differ from approved suppression/sizing')
            if copied.select('listing_id','time_us').is_duplicated().any():raise ValueError('Duplicate label identity')
            if copied.filter(~pl.col('label_value').is_between(0,1) | ~pl.col('allocation_ratio').is_finite()).height:raise ValueError('Invalid target values')
            for name in ('episodes','pairs','trades'):
                if file_hash(folder/(name+'.parquet'))!=receipt['files'][name]['sha256']:raise ValueError('Copied sidecar changed')
            rows+=copied.height;suppressed+=receipt['suppressed_rows'];totals['shards']+=1
        if rows!=entry['rows'] or rows!=proof['rows'] or suppressed!=proof['suppressed_rows']:raise ValueError('Read-back coverage mismatch')
        totals['rows']+=rows;totals['suppressed_rows']+=suppressed;totals['selected']+=selected.height;totals['groups']+=len(proof['groups'])
    if totals['rows']!=dataset['rows']:raise ValueError('Dataset coverage mismatch')
    return dict(status='passed',version=VERSION,binding=dataset['binding'],day_certificates={e['day']:e['sha256'] for e in dataset['days']},**totals)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--runtime-root',type=Path,default=Path('D:/TradingML/runtimes'))
    parser.add_argument('--source-commit',required=True)
    parser.add_argument('--validation-extension',action='store_true',help='Generation/integrity only; never publish to the audit UI')
    args=parser.parse_args(argv);runtime=args.runtime_root.resolve();output=args.output.resolve()
    if not runtime.is_dir() or not output.is_relative_to(runtime):raise ValueError('Explicit runtime required')
    if len(args.source_commit)!=40 or any(c not in '0123456789abcdef' for c in args.source_commit):raise ValueError('Exact pushed source commit required')
    output.mkdir(parents=True,exist_ok=True)
    # OS-level exclusive writer lock; released even after process termination.
    from scripts.backfill_trade_reporting_flags import exclusive
    with exclusive(output/'writer'):
        source.write_json(output/'progress.json',dict(status='running',stage='verifying_immutable_1a'))
        original=source.require_dataset(args.source_dataset,runtime_root=runtime,allow_extension=args.validation_extension)
        if bool(original.get('validation_split'))!=args.validation_extension:raise ValueError('Explicit extension mode must match source dataset')
        source_hash=file_hash(args.source_dataset);config=Config()
        binding=dict(version=VERSION,grouping=GROUPING,source_sha256=source_hash,source_commit=args.source_commit,config=vars(config),
            calculation_sha256=file_hash(Path(__file__)),grouping_sha256=file_hash(Path(__file__).with_name('market_teacher_preview.py')))
        source.write_json(output/'manifest.json',dict(model_family='rl_trading',version=VERSION,job_type='prepare_market_teacher_1b',
            git_commit=args.source_commit,args={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
            config=vars(config),binding=binding,output_root=str(output),training_started=False))
        entries=[original['context']]+original['days'];completed=[];total_rows=0
        for index,entry in enumerate(entries):
            day=entry['day'];root=Path(entry['teacher_root']);dayroot=output/day
            proof=source.verify_day(root,entry['bank_certificate_sha256'],verify_files=entry['role']=='context_only')
            dayroot.mkdir(exist_ok=True)
            def progress(stage,**extra):
                source.write_json(output/'progress.json',dict(status='running',stage=stage,completed_days=index,total_days=len(entries),**{'day':day,**extra}))
            progress('session_selection')
            candidates=[];pairs=[]
            for item in proof['shards']:
                folder=root/item['path']
                candidates.append(pl.scan_parquet(folder/'labels.parquet').filter(pl.col('action')=='ENTRY').select('listing_id','pair_id','time_us','close','entry_gain','entry_target_us','action').collect())
                pairs.append(pl.read_parquet(folder/'pairs.parquet'))
            decisions=select(pl.concat(candidates,how='diagonal_relaxed'),pl.concat(pairs,how='diagonal_relaxed'),config)
            members,groups=group(decisions.filter(pl.col('selected')),config)
            decisions=decisions.join(members.select('listing_id','pair_id','group_id','allocation_ratio','active_score_sum','active_count'),on=['listing_id','pair_id'],how='left',validate='1:1')
            decision_path=dayroot/'decisions.parquet'
            if decision_path.exists():
                if not pl.read_parquet(decision_path).equals(decisions):raise ValueError('Immutable session decisions changed')
            else:decisions.write_parquet(decision_path)
            shards=[];suppressed=rows=0
            for number,item in enumerate(proof['shards']):
                src=root/item['path'];dest=dayroot/item['path'];dest.mkdir(parents=True,exist_ok=True)
                progress('copying_shards',completed_shards=number,total_shards=len(proof['shards']))
                pinned=dict(**binding,source_shard_sha256=item['sha256'])
                marker=dest/'complete.json'
                if marker.exists():
                    receipt=json.loads(marker.read_text())
                    if receipt['binding']!=pinned:raise ValueError('Existing 1b shard belongs to another immutable calculation')
                    for name,record in receipt['files'].items():
                        if file_hash(dest/(name+'.parquet'))!=record['sha256']:raise ValueError('Existing 1b shard changed')
                else:
                    frame=pl.read_parquet(src/'labels.parquet');copied=copy_labels(frame,decisions)
                    unchanged=[c for c in frame.columns if c not in ('action','label_value','reference_action')]
                    if copied.height!=frame.height or not copied.select(unchanged).equals(frame.select(unchanged)):raise ValueError('1b copy changed source rows or audit fields')
                    rejected=copied.filter(pl.col('episode_selected').eq(False))
                    if rejected.filter(pl.col('action')!='WAIT').height or rejected.filter(pl.col('allocation_loss_mask')).height:raise ValueError('Rejected episode supervision leaked')
                    if copied.filter(pl.col('allocation_loss_mask') & ~pl.col('allocation_ratio').is_between(0,1)).height:raise ValueError('Invalid sizing target')
                    copied.write_parquet(dest/'labels.parquet');files={'labels':dict(sha256=file_hash(dest/'labels.parquet'),rows=copied.height)}
                    for name in ('episodes','pairs','trades'):
                        shutil.copyfile(src/(name+'.parquet'),dest/(name+'.parquet'))
                        files[name]=dict(sha256=file_hash(dest/(name+'.parquet')),rows=pl.scan_parquet(dest/(name+'.parquet')).select(pl.len()).collect().item())
                    for name in ('episodes','pairs','trades'):
                        if files[name]['sha256']!=json.loads((src/'complete.json').read_text())['files'][name]['sha256']:raise ValueError('1a sidecar changed during copy')
                    receipt=dict(version=VERSION,binding=pinned,files=files,rows=copied.height,
                        suppressed_rows=copied.filter(pl.col('action')!=pl.col('action_1a')).height,identities=json.loads((src/'complete.json').read_text())['identities'],status='audited_1b_shard')
                    source.write_json(marker,receipt)
                rows+=receipt['rows'];suppressed+=receipt['suppressed_rows'];shards.append(dict(path=item['path'],sha256=file_hash(marker)))
            if rows!=proof['valid_rows']:raise ValueError('1b day coverage changed')
            dayproof=dict(version=VERSION,binding=binding,day=day,role=entry['role'],rows=rows,suppressed_rows=suppressed,selected=members.height,
                groups=groups,shards=shards,decisions_sha256=file_hash(dayroot/'decisions.parquet'),source_teacher_sha256=entry['teacher_sha256'],sealed_test_accessed=False)
            source.write_json(dayroot/'complete.json',dayproof)
            completed.append(dict(day=day,role=entry['role'],root=str(dayroot),sha256=file_hash(dayroot/'complete.json'),rows=rows));total_rows+=rows
        dataset=dict(version=VERSION,status='audited_1b_labels',binding=binding,source_dataset=str(args.source_dataset),action_order=ACTION_ORDER,
            days=completed,rows=total_rows,sealed_test_accessed=False,training_started=False)
        if args.validation_extension:
            dataset['validation_split']=original['validation_split']
            dataset['sealed_labels_generated']=True
            dataset['sealed_access_policy']=original['sealed_access_policy']
        audit=audit_dataset(dataset,original,progress)
        if file_hash(args.source_dataset)!=source_hash:raise ValueError('1a source dataset changed')
        source.write_json(output/'publication-audit.json',audit)
        dataset['publication_audit']=str(output/'publication-audit.json');dataset['publication_audit_sha256']=file_hash(output/'publication-audit.json')
        dataset['hash']=digest(dataset);source.write_json(output/'dataset.json',dataset)
        if not args.validation_extension:source.write_json(runtime/'rl-v6-active-market-teacher.json',dict(version=VERSION,dataset=str(output/'dataset.json'),sha256=file_hash(output/'dataset.json')))
        source.write_json(output/'progress.json',dict(status='complete',completed_days=len(entries),total_days=len(entries),rows=total_rows))
    return 0

if __name__=='__main__':raise SystemExit(main())
