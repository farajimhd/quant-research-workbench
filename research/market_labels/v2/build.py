"""Restart-safe spread-aware sidecars from sealed 1a labels and quote bars."""
from dataclasses import asdict
import json
from pathlib import Path
import numpy as np
import polars as pl
from research.market_shards.v1.data import Session
from research.market_shards.v1.contract import sha
from research.market_labels.v1.build import write_json, verify_day as verify_original
from research.rl_trading.v6.market_teacher_preview import Config, group
from .contract import VERSION, SOURCE_SHA, SCOPE
from .selection import quote_book, price_rows, select, copy_targets

ALGORITHM='preserved-approved-v66-1a'
GROUPING='spread-qualified-half-open-1b-v2'


def fingerprint():
    root=Path(__file__).resolve().parents[3]
    names=['research/market_labels/v2/'+n for n in ('build.py','run_build.py','selection.py','contract.py')]
    names+=['research/market_labels/v1/build.py','research/market_shards/v1/data.py','research/market_shards/v1/contract.py',
        'research/rl_trading/v6/market_teacher_preview.py']
    return {n:sha(root/n) for n in names}


def verify_day(root,binding=None):
    root=Path(root);proof=json.loads((root/'complete.json').read_bytes())
    if proof['version']!=VERSION or (binding is not None and proof['binding']!=binding):raise ValueError('Spread label binding changed')
    for name,record in proof['files'].items():
        if sha(root/name)!=record['sha256']:raise ValueError('Spread label bytes changed: '+name)
        if pl.scan_parquet(root/name).select(pl.len()).collect().item()!=record['rows']:raise ValueError('Spread label row count changed')
    if proof['source_rows']!=proof['valid_rows']+proof['excluded_rows']:raise ValueError('Source census changed')
    return proof


def build_day(task):
    manifest,day,role,output,binding,original_dataset=task
    root=Path(output)/day
    if (root/'complete.json').exists():return verify_day(root,binding)
    root.mkdir(parents=True,exist_ok=True)
    original_dataset=Path(original_dataset)
    if sha(original_dataset)!=binding['input_labels_sha256']:raise ValueError('Original label dataset changed')
    dataset=json.loads(original_dataset.read_bytes())
    entry=next(d for d in dataset['days'] if d['day']==day and d['role']==role)
    source=original_dataset.parent/day
    if sha(source/'complete.json')!=entry['certificate']:raise ValueError('Original label certificate changed')
    original=verify_original(source,dataset['binding'])
    if dataset['binding']['source_manifests'][role]!=sha(manifest):raise ValueError('Quote/label manifest mismatch')
    session=Session(manifest,day);names=sorted(session.item['selected'])
    if set(names)!={r['ticker'] for r in original['listings']}:raise ValueError('Label/quote ticker universe differs')
    quotes=session._read(day,'quote-1000',names)
    if quotes is None:raise ValueError('Sealed 1s quote bars missing; no database fallback')
    book=quote_book(quotes)
    frames=[];records=[]
    dropped=['action','reference_action','label_value','episode_selected','group_id','allocation_ratio','active_score_sum','active_count','allocation_loss_mask','teacher_probabilities']
    for i,record in enumerate(original['listings']):
        record=dict(record);records.append(record)
        if record['valid_rows']:
            frame=pl.read_parquet(source/record['file']).drop(dropped,strict=False).with_columns(
                pl.col('action_1a').alias('action'),pl.col('reference_action_1a').alias('reference_action'),pl.col('label_value_1a').alias('label_value'))
            frames.append(frame)
        write_json(root/'progress.json',dict(day=day,status='read preserved 1a',completed=i+1,total=len(names)))
    if not frames:raise ValueError('Session has no preserved label rows')
    rows=pl.concat(frames,how='vertical_relaxed')
    cfg=original['config'];sel=Config(**original['selection'])
    priced=price_rows(rows,book,cfg['half_life_seconds'],sel)
    pairs=pl.read_parquet(source/'pairs.parquet')
    decisions=select(priced,pairs)
    members,groups=group(decisions.filter(pl.col('selected')),sel)
    decisions=decisions.join(members.select('listing_id','pair_id','group_id','allocation_ratio','active_score_sum','active_count'),
        on=['listing_id','pair_id'],how='left',validate='1:1').with_columns(pl.col('allocation_ratio').fill_null(0.))
    copied=copy_targets(priced,decisions,cfg['half_life_seconds'],cfg['minimum_position_seconds'],sel)
    if copied.height!=rows.height or not copied.select(rows.columns).drop('action','reference_action','label_value').equals(rows.drop('action','reference_action','label_value')):
        raise ValueError('Spread copy changed original 1a data')
    if copied.filter((pl.col('episode_selected')==False)&(pl.col('action')!='WAIT')).height:raise ValueError('Rejected pair leaked')
    if copied.filter((pl.col('action')=='ENTRY')&~pl.col('spread_entry_eligible')).height:raise ValueError('Unqualified ENTRY leaked')
    if copied.filter((pl.col('action')=='EXIT')&~pl.col('spread_exit_eligible')).height:raise ValueError('Unqualified EXIT leaked')
    # Every chosen target must survive as a supported executable-price EXIT.
    witnesses=copied.filter(pl.col('spread_exit_eligible') & pl.col('episode_selected')).select('listing_id','pair_id',pl.col('time_us').alias('entry_target_us'))
    if members.join(witnesses,on=['listing_id','pair_id','entry_target_us'],how='anti').height:raise ValueError('Selected target lacks a valid EXIT')
    if members.height:
        starts=members['time_us'].to_numpy();ends=members['entry_target_us'].to_numpy();scores=members['selection_score'].to_numpy()
        def total(times):
            order=np.argsort(times,kind='stable');return np.r_[0.,np.cumsum(scores[order])][np.searchsorted(times[order],starts,side='right')]
        if not np.allclose(total(starts)-total(ends),members['active_score_sum'].to_numpy(),rtol=1e-9,atol=1e-9):raise ValueError('Spread active-set mismatch')
    files={}
    def save(name,frame):
        frame.write_parquet(root/name,compression='zstd');files[name]=dict(rows=frame.height,sha256=sha(root/name))
    for record in records:
        if not record['valid_rows']:continue
        frame=copied.filter(pl.col('listing_id')==record['listing_id'])
        record['actions_1b']=frame.group_by('action').len().sort('action').to_dicts()
        save(record['file'],frame)
    for name in ('episodes','pairs','trades','excluded'):save(name+'.parquet',pl.read_parquet(source/(name+'.parquet')))
    save('decisions.parquet',decisions)
    reasons=priced.filter(pl.col('action_1a')=='ENTRY').group_by('spread_entry_reason').len().sort('spread_entry_reason').to_dicts()
    quote_audit=dict(source='quote-1000.parquet',sha256=session.seals[day]['files']['quote-1000.parquet'],rows=quotes.height,
        tickers=quotes['ticker'].n_unique(),universe=len(names),tickers_without_quotes=sorted(set(names)-set(quotes['ticker'].unique().to_list())),label_rows=priced.height,valid_quote_rows=priced['spread_quote_valid'].fill_null(False).sum(),entry_reasons=reasons,
        old_selected_pairs=original['selected_pairs'],new_selected_pairs=members.height,masked_rows=copied.filter(pl.col('action')=='UNLABELLED').height)
    proof=dict(original,version=VERSION,binding=binding,scope=SCOPE,selection=asdict(sel),files=files,listings=records,groups=groups,
        selected_pairs=members.height,quote_audit=quote_audit,status='verified_review_only')
    write_json(root/'complete.json',proof)
    return verify_day(root,binding)
