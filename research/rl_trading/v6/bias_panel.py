"""Certified, indexed strict-prior windows for the laptop bias campaign.

Full source banks are authenticated once before a declared ticker/time subset.
The local frozen panel contains no sealed labels and preserves every decision
in the requested interval. Training and calibration are separate chronological
folds; calibration remains a training-role day, never a sealed test.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import argparse
from datetime import datetime
import gc
import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo
import numpy as np
import torch

from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6.model import INPUT_WIDTH
from research.rl_trading.v6.execution_features import bps_input

VERSION = 'rl-v6-bias-panel-strict-prior-v1'
PRICE_HISTORY_VERSION = 'rl-v6-bias-panel-valid-price-prior-v2'
PRICE_DIVERSITY_VERSION = 'rl-v6-bias-panel-valid-price-train-diversity-v3'
TICKERS = ('AAPL','NVDA','MU','CYCU','SNDK')
FOLDS = {'train': ('2026-07-31','2026-08-03','2026-08-04'),
         'calibration': ('2026-08-21',), 'development': ('2026-08-24','2026-08-25')}
EXTENDED_FOLDS=dict(FOLDS,development=FOLDS['development']+('2026-08-27','2026-08-28','2026-09-01','2026-09-03'))
DIVERSITY_FOLDS=dict(train=FOLDS['train']+('2026-08-05','2026-08-06','2026-08-07'),
    calibration=('2026-08-10','2026-08-11'),development=EXTENDED_FOLDS['development'])

def admitted_folds(manifest):
    if manifest.get('version')==PRICE_DIVERSITY_VERSION:
        if manifest.get('input_history_contract',{}).get('price_valid_only') is not True:
            raise ValueError('Diversity version requires valid-price history')
        return DIVERSITY_FOLDS
    return EXTENDED_FOLDS if manifest.get('extended_public_development',False) else FOLDS


def indexed_panel(session, decisions, *, valid_price_history=False):
    """No current candle enters a window or cross-ticker market snapshot."""
    features=[np.zeros((1,INPUT_WIDTH),np.float32)];clocks=[];row_ids=[];cursor=1
    for identity in session.listings:
        sources=[]
        if session.previous and identity in session.previous.manifest['offsets']:
            sources.append(session.previous.listing(identity) if valid_price_history else session.previous.listing_tail(identity))
        sources.append(session.bank.listing(identity))
        values=[];times=[]
        for source in sources:
            values.append(bps_input(torch.from_numpy(np.array(source.scalar,copy=True)),
                torch.from_numpy(np.array(source.levels,copy=True))).numpy())
            times.append(source.close_us)
        x=np.concatenate(values);t=np.concatenate(times)
        if len(t)>1 and (np.diff(t)<=0).any():raise ValueError('Unordered actual candles')
        if valid_price_history:
            selected=np.flatnonzero(x[:,35]>.5)
            x=x[selected].copy();t=t[selected]
            if len(t)>1:x[1:,26]=np.log1p(np.diff(t).astype(np.float64)/1_000_000)
        features.append(x);clocks.append(t);row_ids.append(np.arange(cursor,cursor+len(x),dtype=np.int32));cursor+=len(x)
    features=np.concatenate(features);n=len(decisions)
    if n>500000 or features.nbytes>800_000_000:raise ValueError('Diagnostic panel budget exceeded')
    windows=np.zeros((n,120),np.int32);held=np.zeros((n,11),np.float32)
    action=np.zeros(n,np.int64);weights=np.zeros(n,np.float32)
    future=np.full((n,5),-1,np.int64);quality=np.zeros(n,np.float32)
    ratios=np.full(n,np.nan,np.float32);identities=np.zeros(n,np.int64)
    decision_clocks=np.asarray([d.close_us for d in decisions],np.int64)
    maximum_input=np.full(n,-1,np.int64);market=np.zeros((n,INPUT_WIDTH),np.float32)
    for i,(times,ids) in enumerate(zip(clocks,row_ids)):
        end=np.searchsorted(times,decision_clocks,side='left');good=end>0
        market[good]+=features[ids[end[good]-1]]
    market/=len(session.listings)
    for r,d in enumerate(decisions):
        holding=bool(len(d.held_index));i=int(d.held_index[0]) if holding else d.soft_tokens[1]-1
        identities[r]=i
        end=int(np.searchsorted(clocks[i],d.close_us,side='left'))
        ids=row_ids[i][max(0,end-120):end]
        if len(ids):windows[r,-len(ids):]=ids;maximum_input[r]=clocks[i][end-1]
        if maximum_input[r]>=d.close_us:raise ValueError('Target or future candle in observation')
        action[r]=(3 if d.token==1+len(session.listings) else 2) if holding else (0 if d.token else 1)
        if holding:held[r]=d.held_features[0]
        weights[r]=d.sample_weight
        if d.forecast_actions is not None:future[r,:len(d.forecast_actions)]=d.forecast_actions
        quality[r]=d.soft_probabilities[0] if holding else d.soft_probabilities[1]
        if d.allocation_ratio_target is not None:ratios[r]=d.allocation_ratio_target
    if not np.isfinite(features).all() or not np.isfinite(market).all():raise ValueError('Nonfinite causal input')
    return dict(features=features,windows=windows,market=market,held=held,action=action,
        weight=weights,future=future,quality=quality,ratio=ratios,identity=identities,
        clock=decision_clocks,max_input_clock=maximum_input,episode=[d.episode_uid for d in decisions])


def combine(items):
    cursor=1;features=[np.zeros((1,INPUT_WIDTH),np.float32)];windows=[]
    for item in items:
        w=item['windows'].copy();w[w>0]+=cursor-1
        features.append(item['features'][1:]);windows.append(w);cursor+=len(item['features'])-1
    out={k:np.concatenate([d[k] for d in items]) for k in items[0] if k not in ('features','windows','episode')}
    out.update(features=np.concatenate(features),windows=np.concatenate(windows),episode=sum([d['episode'] for d in items],[]))
    return out


def normalization(panel):
    # Unique source candles, not overlapping-window repetitions. Train only.
    used=np.unique(panel['windows']);used=used[used>0]
    if not len(used):raise ValueError('Training history is empty')
    x=panel['features'][used].astype(np.float64);mean=x.mean(0);std=x.std(0)
    std=np.where(std>1e-6,std,1.)
    from research.rl_trading.v6.features import SCALAR_NAMES,LEVEL_NAMES
    for i,name in enumerate(SCALAR_NAMES):
        if name.endswith(('valid','present','available')) or name in ('premarket','regular','after_hours'):
            mean[i]=0.;std[i]=1.
    for slot in range(10):
        for name in ('role_support','role_resistance','role_transition','historical_origin','present'):
            i=37+slot*11+LEVEL_NAMES.index(name);mean[i]=0.;std[i]=1.
    return dict(mean=mean.tolist(),std=std.tolist(),observations=len(x),scope='train_only',units='bps-v1')


def verify_panel(panel, manifest):
    for fold,values in panel.items():
        if fold not in FOLDS:raise ValueError('Unexpected panel fold')
        if (values['max_input_clock']>=values['clock']).any():raise ValueError('Panel causal fence changed')
        if not np.isin(values['action'],[0,1,2,3]).all():raise ValueError('Unknown action')
        if (values['windows']<0).any() or values['windows'].max()>=len(values['features']):raise ValueError('Window rows escaped source')
        if not np.isfinite(values['features']).all():raise ValueError('Nonfinite panel')
    admitted=admitted_folds(manifest)
    if manifest['folds']!={k:list(v) for k,v in admitted.items()} or manifest['sealed_labels_read'] is not False:
        raise ValueError('Frozen public-only panel scope changed')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seconds',type=int,default=3600)
    parser.add_argument('--tickers',nargs='+',default=list(TICKERS))
    parser.add_argument('--extended-public-development',action='store_true')
    parser.add_argument('--training-diversity',action='store_true',help='Fixed six TRAIN mornings and two later TRAIN-role calibration mornings; all public development is exploratory')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--valid-price-history',action='store_true',help='Versioned diagnostic: last 120 priced candles, elapsed clock gaps; source labels/indicators unchanged')
    parser.add_argument('--plan-only',action='store_true',help='Freeze fresh scope/source binding before explicitly verified reuse of exports')
    args=parser.parse_args(argv)
    root=Path('D:/TradingML/runtimes').resolve();output=args.output.resolve()
    if not root.is_dir() or not output.is_relative_to(root) or (output.exists() and not args.resume):raise ValueError('Fresh laptop runtime or explicit resume required')
    if args.seconds not in (3600,14400) or not 1<=len(args.tickers)<=20 or len(set(args.tickers))!=len(args.tickers):raise ValueError('Bounded predeclared ticker/time scope required')
    if args.training_diversity and (not args.valid_price_history or args.extended_public_development):
        raise ValueError('Diversity scope requires valid prices and its own explicit fold contract')
    folds=DIVERSITY_FOLDS if args.training_diversity else EXTENDED_FOLDS if args.extended_public_development else FOLDS
    from research.mlops.env import discover_env_files,load_env_files
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    from research.rl_trading.v6 import saved_label_audit as source,published_market_audit as market
    from research.rl_trading.v6.session_data import open_session
    from research.rl_trading.v6.opportunity_dataset import load_teacher
    from research.rl_trading.v6.probe_teacher_sequence import subset
    output.mkdir(parents=True,exist_ok=args.resume)
    manifest=dict(version=PRICE_DIVERSITY_VERSION if args.training_diversity else PRICE_HISTORY_VERSION if args.valid_price_history else VERSION,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        source_files_sha256={p.name:file_hash(p) for p in Path(__file__).parent.glob('*.py')},
        folds={k:list(v) for k,v in folds.items()},tickers=args.tickers,seconds=args.seconds,extended_public_development=args.extended_public_development,
        sealed_labels_read=False,scope=f'bounded-{len(args.tickers)}-ticker-local-window-diagnostic-not-full-market-policy',sessions={})
    if args.training_diversity:manifest['development_status']='previously_inspected_exploratory_not_independent_validation'
    if args.valid_price_history:manifest['input_history_contract']=dict(price_valid_only=True,max_candles=120,gaps='elapsed_seconds',source_indicators='unchanged',target_candle='strictly_excluded',ranking='local_diagnostic_only_not_production_ranker')
    planned=output/'planned.json'
    if planned.exists():
        old=json.loads(planned.read_text())
        if {k:v for k,v in old.items() if k!='source_commit'}!={k:v for k,v in manifest.items() if k!='source_commit'}:raise ValueError('Resume source/scope binding changed')
        manifest=old
    else:planned.write_text(json.dumps(manifest,indent=2))
    scope_hash=file_hash(planned)
    if args.plan_only:return 0
    prepared={}
    for fold,days in folds.items():
        items=[]
        for day in days:
            cache=output/(day+'.pt');receipt=output/(day+'-receipt.json')
            if receipt.exists():
                proof=json.loads(receipt.read_text())
                if proof['scope_sha256']!=scope_hash or proof['fold']!=fold or file_hash(cache)!=proof['sha256']:raise ValueError('Cached session binding changed')
                items.append(torch.load(cache,map_location='cpu',weights_only=False));manifest['sessions'][day]=proof['session']
                print('Reused authenticated immutable local panel:',day,flush=True)
                continue
            print('Authenticating full current/prior banks:',fold,day,flush=True)
            active,entry,_,teacher,bankroot,_=source.session(day)
            expected='development' if fold=='development' else 'train'
            if entry['role']!=expected:raise ValueError('Panel role admission mismatch')
            ids=sorted(i for i,t in source.saved_symbols(str(bankroot),entry['bank_certificate_sha256']).items() if t in args.tickers)
            if len(ids)!=len(args.tickers):raise ValueError('Diagnostic ticker identity is ambiguous/missing')
            full=open_session(bankroot,runtime_root=source.runtime(),previous_root=source.mapped(entry['previous_root']),
                split_manifest=source.mapped(entry['split_manifest']) if entry.get('split_manifest') else None,split_mapper=source.mapped)
            ma,_,me,mroot,_=market.session(day)
            labels,_=load_teacher(teacher,full,runtime_root=source.runtime(),audit_development=True,audit_listing_ids=ids,market_root=mroot)
            begin=int(datetime.fromisoformat(day+'T04:00:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp()*1_000_000)
            packed,selected=subset(full,labels,ids,begin,begin+args.seconds*1_000_000-1,valid_price_context=args.valid_price_history)
            item=indexed_panel(packed,selected,valid_price_history=args.valid_price_history);items.append(item)
            manifest['sessions'][day]=dict(role=expected,identities=ids,begin_us=begin,end_us=begin+args.seconds*1_000_000,
                rows=len(selected),counts=np.bincount(item['action'],minlength=4).tolist(),
                bank_certificate_sha256=entry['bank_certificate_sha256'],dataset_sha256=active['sha256'],
                market_dataset_sha256=ma['sha256'],market_certificate_sha256=me['sha256'],context_split_receipt_sha256=packed.context_split_receipt_sha256)
            torch.save(item,cache)
            receipt.write_text(json.dumps(dict(scope_sha256=scope_hash,fold=fold,sha256=file_hash(cache),session=manifest['sessions'][day]),indent=2))
            (output/'progress.json').write_text(json.dumps(dict(phase='preparing',completed=list(manifest['sessions']),last=manifest['sessions'][day]),indent=2))
            print(day,manifest['sessions'][day]['counts'],flush=True)
            del full,labels,packed,selected;gc.collect()
        prepared[fold]=combine(items);del items;gc.collect()
    if args.training_diversity:
        for key in ('dataset_sha256','market_dataset_sha256'):
            if len({s[key] for s in manifest['sessions'].values()})!=1:
                raise ValueError('Diversity panel cannot mix published dataset identities')
    manifest['normalization']=normalization(prepared['train']);manifest['hash']=digest(manifest)
    verify_panel(prepared,manifest)
    torch.save(prepared,output/'panel.pt')
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    (output/'complete.json').write_text(json.dumps(dict(status='prepared',version=manifest['version'],
        manifest_sha256=file_hash(output/'manifest.json'),panel_sha256=file_hash(output/'panel.pt'),
        counts={k:np.bincount(v['action'],minlength=4).tolist() for k,v in prepared.items()},sealed_labels_read=False),indent=2))
    print('Prepared authenticated panel',output,flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
