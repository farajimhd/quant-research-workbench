"""Versioned episode opportunity labels, independent of portfolio selection.

Only sparse target tables are generated. Certified candle banks are reused;
future prices/scores occur on the label side, never in observations.
"""
from dataclasses import dataclass, asdict
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import polars as pl

from research.rl_trading.v6.allocation import MIN_SCORE
from research.rl_trading.v6.features import SCALAR_NAMES
from research.rl_trading.v6.session_data import open_session
from research.rl_trading.v6.training_gate import require_dataset

VERSION = 'rl-v6-independent-episode-windows-rolling15-v1'


@dataclass(frozen=True)
class WindowConfig:
    seconds: int = 15
    min_score: float = MIN_SCORE
    min_peak_headroom: float = .01
    fee_per_share: float = .005
    initial_cash: float = 10_000.

    def __post_init__(self):
        if (not 1 <= self.seconds <= 300 or not 0 < self.min_score < 1 or
            not 0 < self.min_peak_headroom < 1 or self.fee_per_share < 0 or
            self.initial_cash <= 0 or not all(np.isfinite(x) for x in
                (self.min_score, self.min_peak_headroom, self.fee_per_share, self.initial_cash))):
            raise ValueError('Invalid episode window settings')


def rolling_allocation(candidates, config=WindowConfig()):
    """Rolling [t,t+15s) teacher allocation, one score per episode.

    Uses the maximum qualifying candidate score per episode in each window,
    so repeated candidate timestamps never multiply an episode's share.
    Future scores are hindsight teacher sizing targets, not live inputs.
    Returns all window members, including future members; no cash pruning.
    """
    required = {'episode_uid','time_us','score','direction'}
    if not required <= set(candidates.columns):
        raise ValueError('Missing sparse opportunity columns')
    if candidates.select('episode_uid','time_us').n_unique() != candidates.height:
        raise ValueError('Duplicate episode candidate timestamp')
    if candidates.filter(~pl.col('score').is_finite() | pl.col('score').is_null()).height:
        raise ValueError('Nonfinite candidate score')
    source = candidates.filter((pl.col('direction') == 1) & (pl.col('score') >= config.min_score))
    if source.is_empty():
        return pl.DataFrame(schema={'decision_us':pl.Int64,'episode_uid':pl.String,
            'allocation_score':pl.Float64,'allocation_weight':pl.Float64,'desired_budget':pl.Float64})
    anchors = source.select(pl.col('time_us').alias('decision_us')).unique()
    members = anchors.join_where(source.select('episode_uid','time_us','score'),
        pl.col('time_us') >= pl.col('decision_us'),
        pl.col('time_us') < pl.col('decision_us') + config.seconds*1_000_000)
    weights = (members.group_by('decision_us','episode_uid')
        .agg(pl.col('score').max().alias('allocation_score'))
        .with_columns((pl.col('allocation_score') / pl.col('allocation_score').sum().over('decision_us'))
                      .alias('allocation_weight'))
        .with_columns((config.initial_cash*pl.col('allocation_weight')).alias('desired_budget'))
        .sort(['decision_us','allocation_score','episode_uid'], descending=[False,True,False]))
    totals = weights.group_by('decision_us').agg(pl.col('allocation_weight').sum().alias('total'))
    if totals.filter((pl.col('total')-1).abs()>1e-12).height or weights.filter(pl.col('allocation_weight')<=0).height:
        raise ValueError('Rolling shares do not conserve allocation budget')
    return weights


def opportunity_windows(episodes, candidates, bars, config=WindowConfig()):
    """All qualifying long episodes, without allocation/portfolio filtering.

    Flat branch: ENTER vs WAIT at each observed candle, scored from existing
    entry candidates. Held branch: EXIT vs ticker HOLD after a hypothetical
    first qualified entry; exit quality is fee-adjusted profit / best profit.
    The branch account snapshots are reconstructed by the loader, not joins
    to an unrelated portfolio. No counterfactual fills are claimed executable.
    """
    long = episodes.filter(pl.col('direction') == 1).with_columns(
        (pl.col('session_date')+':'+pl.col('listing_id')+':'+pl.col('episode_id').cast(pl.String)).alias('episode_uid'))
    if long['episode_uid'].n_unique()!=long.height or bars.select('listing_id','time_us').n_unique()!=bars.height:
        raise ValueError('Ambiguous episode or candle identity')
    qualifying = candidates.filter((pl.col('direction') == 1) & (pl.col('score') >= config.min_score))
    if qualifying.is_empty():
        raise ValueError('No qualifying long episodes')
    accepted = long.join(qualifying.select('episode_uid').unique(), on='episode_uid', validate='1:1')
    paths = accepted.join_where(bars,
        pl.col('listing_id') == pl.col('listing_id_right'),
        pl.col('time_us') >= pl.col('entry_hint_us'),
        pl.col('time_us') < pl.col('end_us'))
    if paths.filter(~pl.col('close').is_finite() | ~pl.col('high').is_finite() |
                    (pl.col('close')<=0) | (pl.col('high')<pl.col('close'))).height:
        raise ValueError('Invalid certified candle path')
    peak = paths.group_by('episode_uid').agg(pl.col('high').max().alias('episode_max_high'))
    entries = qualifying.join(peak,on='episode_uid',validate='m:1').with_columns(
        (pl.col('episode_max_high')/pl.col('decision_close')-1).alias('peak_headroom'))
    # This guards episode geometry only; the existing fee-aware score threshold
    # must also pass. Being below a past peak alone does not make entry good.
    entries = entries.filter(pl.col('peak_headroom') >= config.min_peak_headroom)
    entry_stats = entries.group_by('episode_uid').agg(pl.col('score').max().alias('best_entry_score'),
        pl.col('time_us').min().alias('hypothetical_entry_us'))
    flat = (paths.join(entries.select('episode_uid','time_us','score','peak_headroom'),
        on=['episode_uid','time_us'],how='left',validate='1:1')
        .join(entry_stats.select('episode_uid','best_entry_score'),on='episode_uid',how='left',validate='m:1')
        .with_columns((pl.col('score')/pl.col('best_entry_score')).fill_null(0).clip(0,1).alias('enter_probability')))
    first = entries.join(entry_stats.select('episode_uid','hypothetical_entry_us'),on='episode_uid')
    first = first.filter(pl.col('time_us')==pl.col('hypothetical_entry_us')).select(
        'episode_uid', 'hypothetical_entry_us',pl.col('decision_close').alias('hypothetical_entry_price'))
    held = paths.join(first,on='episode_uid',validate='m:1').filter(
        pl.col('time_us')>pl.col('hypothetical_entry_us')).with_columns(
        (pl.col('close')-pl.col('hypothetical_entry_price')-2*config.fee_per_share).alias('exit_net_per_share'))
    held = held.with_columns(pl.col('exit_net_per_share').max().over('episode_uid').alias('best_exit_net'))
    held = held.with_columns(pl.when((pl.col('time_us')-pl.col('hypothetical_entry_us')>=3_000_000) &
        (pl.col('best_exit_net')>0)).then((pl.col('exit_net_per_share')/pl.col('best_exit_net')).clip(0,1))
        .otherwise(0).alias('exit_probability'))
    # A real observed final candle is the liquidation boundary of this label
    # branch. Exits are not fabricated in missing clock seconds.
    held = held.with_columns(pl.when(pl.col('time_us')==pl.col('time_us').max().over('episode_uid'))
        .then(1.).otherwise(pl.col('exit_probability')).alias('exit_probability'))
    flat = flat.select('episode_uid','listing_id','time_us','close','enter_probability','peak_headroom')
    held = held.select('episode_uid','listing_id','time_us','close','hypothetical_entry_us',
                       'hypothetical_entry_price','exit_probability')
    # Normalize each branch's total label mass per episode, not per-second:
    # longer episodes do not receive a larger overall optimization budget.
    flat = flat.with_columns((1/pl.len().over('episode_uid')).alias('sample_weight'))
    held = held.with_columns((1/pl.len().over('episode_uid')).alias('sample_weight'))
    return flat.sort('time_us','episode_uid'), held.sort('time_us','episode_uid')


def build_day(session, output, config=WindowConfig()):
    output=Path(output)
    if output.exists():
        raise ValueError('Episode sidecar output already exists')
    source=session.root
    certificate=json.loads((source/'complete.json').read_text())
    frames={}
    for name in ('episodes','candidates'):
        path=source/(name+'.parquet')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=certificate['outputs'][name]['sha256']:
            raise ValueError('Opportunity source hash changed')
        frames[name]=pl.read_parquet(path)
    qualifying=set(frames['candidates'].filter(pl.col('direction')==1)['listing_id'])
    bars=[]
    for identity in sorted(qualifying):
        values=session.bank.listing(identity)
        scalar=values.scalar
        valid=(scalar[:,SCALAR_NAMES.index('bar_price_valid')]==1)&(scalar[:,SCALAR_NAMES.index('bar_extremes_valid')]==1)
        bars.append(pl.DataFrame({'listing_id':[identity]*int(valid.sum()),'time_us':values.close_us[valid],
            'close':np.exp(scalar[valid,SCALAR_NAMES.index('log_close')].astype(np.float64)),
            'high':np.exp(scalar[valid,SCALAR_NAMES.index('log_high')].astype(np.float64))}))
    flat,held=opportunity_windows(frames['episodes'],frames['candidates'],pl.concat(bars),config)
    # Guarded opportunities are the allocator's candidates as well. An entry
    # near the episode peak must not dilute the shares of valid entries.
    allocation=rolling_allocation(frames['candidates'].join(
        flat.filter(pl.col('enter_probability')>0).select('episode_uid','time_us'),
        on=['episode_uid','time_us'],how='inner',validate='1:1'),config)
    flat=flat.join(allocation.select('episode_uid',pl.col('decision_us').alias('time_us'),'allocation_weight'),
        on=['episode_uid','time_us'],how='left',validate='1:1').with_columns(pl.col('allocation_weight').fill_null(0))
    if flat.filter((pl.col('enter_probability')>0)&(pl.col('allocation_weight')<=0)).height:
        raise ValueError('Positive opportunity has no rolling allocation share')
    output.mkdir(parents=True)
    files={}
    for name, frame in (('flat',flat),('held',held),('allocation',allocation)):
        path=output/(name+'.parquet');frame.write_parquet(path)
        files[name]={'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'rows':frame.height}
    expected=frames['candidates'].filter((pl.col('direction')==1)&(pl.col('score')>=config.min_score))['episode_uid'].n_unique()
    if flat['episode_uid'].n_unique()!=expected:
        raise ValueError('Profitable episode supervision silently dropped')
    report=dict(version=VERSION,status='audited_independent_episode_windows',day=str(session.day),role=session.role,
        config=asdict(config),files=files,bank_certificate_sha256=session.source_certificate_sha256,
        candidates_sha256=certificate['outputs']['candidates']['sha256'],episodes_sha256=certificate['outputs']['episodes']['sha256'],
        episodes_supervised=expected,entry_positive_rows=flat.filter(pl.col('enter_probability')>0).height,
        peak_guard_rejected_rows=frames['candidates'].filter((pl.col('direction')==1)&(pl.col('score')>=config.min_score)).height-flat.filter(pl.col('enter_probability')>0).height,
        allocation_scope='rolling_hindsight_target_not_live_feature_no_cash_pruning',
        state_scope='independent_flat_and_hypothetical_held_branches_not_joint_portfolio',sealed_test_accessed=False)
    report['metrics_scope']='local_ticker_entry_vs_wait_and_exit_vs_hold_not_global_selection_accuracy'
    (output/'complete.json').write_text(json.dumps(report,sort_keys=True))
    return report


def load_episode_teacher(root, session, *, runtime_root, audit_development=False):
    from research.rl_trading.v6.opportunity_dataset import load_teacher as current
    return current(root, session, runtime_root=runtime_root, audit_development=audit_development)


def _load_legacy_episode_teacher_for_historical_audit(root, session, *, runtime_root, audit_development=False):
    """Independent flat/one-share held branch labels; not a joint portfolio.

    Future allocation weights are size targets only. Holding cost, current
    mark and age describe an explicitly hypothetical earlier unit entry.
    """
    from research.rl_trading.v6.training import TeacherDecision, _validate
    from dataclasses import replace
    root=Path(root).resolve(); runtime=Path(runtime_root).resolve()
    if not root.is_relative_to(runtime) or session.role not in (('train','development') if audit_development else ('train',)):
        raise ValueError('Episode teacher role/root mismatch')
    certificate=json.loads((root/'complete.json').read_text())
    if (certificate.get('version')!=VERSION or certificate.get('status')!='audited_independent_episode_windows' or
        certificate.get('bank_certificate_sha256')!=session.source_certificate_sha256 or
        certificate.get('day')!=str(session.day) or certificate.get('role')!=session.role or
        certificate.get('sealed_test_accessed') is not False):
        raise ValueError('Episode teacher certificate does not bind to session')
    frames={}
    for name in ('flat','held','allocation'):
        path=root/(name+'.parquet')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=certificate['files'][name]['sha256']:
            raise ValueError('Episode teacher bytes changed')
        frames[name]=pl.read_parquet(path)
        if frames[name].height!=certificate['files'][name]['rows']:
            raise ValueError('Episode target count mismatch')
    by_identity={identity:i for i,identity in enumerate(session.listings)}
    if (set(frames['flat']['listing_id'])|set(frames['held']['listing_id']))-set(by_identity):
        raise ValueError('Episode identity absent from certified bank')
    config=WindowConfig(**certificate['config']);n=len(session.listings);labels=[]
    for branch in ('flat','held'):
        for row in frames[branch].iter_rows(named=True):
            i=by_identity[row['listing_id']];enter=np.zeros(n,bool)
            if branch=='flat':
                p=float(row['enter_probability']);enter[i]=True
                token=1+i if p>=.5 else 0;tokens=(0,1+i);probabilities=(1-p,p)
                held_index=np.empty(0,np.int64);held_features=np.zeros((0,11),np.float32)
                account=np.array([config.initial_cash,config.initial_cash,0,0,0,0,0],np.float32)
                size=float(row['allocation_weight']) if token else None;exit_allowed=np.empty(0,bool)
            else:
                p=float(row['exit_probability']);tokens=(1+n,1+n+3);probabilities=(p,1-p)
                token=tokens[0] if p>=.5 else tokens[1]
                price=float(row['hypothetical_entry_price']);mark=float(row['close'])
                cash=config.initial_cash-price-config.fee_per_share
                if cash<0:raise ValueError('Unit position exceeds hypothetical bankroll')
                equity=cash+mark
                account=np.array([cash,equity,0,mark/equity,(row['time_us']-row['hypothetical_entry_us'])/1e6,0,0],np.float32)
                held_index=np.array([i],np.int64)
                held_features=np.array([[1.,price,(row['time_us']-row['hypothetical_entry_us'])/1e6,
                    (mark-price)/price,0,0,0,0,0,0,0]],np.float32)
                size=None;exit_allowed=np.ones(1,bool)
            labels.append(TeacherDecision(int(row['time_us']),0,token,account,held_index,held_features,
                enter,exit_allowed,np.zeros(len(held_index),bool),np.zeros(len(held_index),bool),
                size_fraction=size,sample_weight=float(row['sample_weight']),soft_tokens=tokens,
                soft_probabilities=probabilities,episode_uid=row['episode_uid']))
    labels.sort(key=lambda d:(d.close_us,d.episode_uid,len(d.held_index)))
    previous=None;order=0;result=[]
    for item in labels:
        if item.close_us!=previous:order=0;previous=item.close_us
        result.append(replace(item,order_index=order));order+=1
    _validate(tuple(result),(),n,wait_hold=True)
    return tuple(result),()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--runtime-root',type=Path,default=Path('D:/TradingML/runtimes'))
    parser.add_argument('--days',nargs='+',required=True)
    parser.add_argument('--resume',action='store_true',help='Verify and reuse completed day sidecars')
    args=parser.parse_args()
    if not args.output.resolve().is_relative_to(args.runtime_root.resolve()) or (args.output.exists() and not args.resume):
        raise ValueError('Runtime sidecar root requires explicit resume when it exists')
    dataset=require_dataset(args.dataset,runtime_root=args.runtime_root)
    selected=[entry for entry in dataset['days'] if entry['day'] in args.days]
    if len(selected)!=len(set(args.days)) or len(set(args.days))!=len(args.days):
        raise ValueError('Only unique audited train/development days permitted')
    for entry in selected:
        existing=args.output/entry['day']/'complete.json'
        if args.resume and existing.is_file():
            proof=json.loads(existing.read_text())
            if (proof.get('version')!=VERSION or proof.get('status')!='audited_independent_episode_windows' or
                proof.get('bank_certificate_sha256')!=entry['bank_certificate_sha256'] or
                proof.get('day')!=entry['day'] or proof.get('role')!=entry['role'] or
                proof.get('config')!=asdict(WindowConfig()) or proof.get('sealed_test_accessed') is not False):
                raise ValueError('Cannot resume changed episode sidecar contract')
            for name in ('flat','held','allocation'):
                path=existing.parent/(name+'.parquet')
                if hashlib.sha256(path.read_bytes()).hexdigest()!=proof['files'][name]['sha256']:
                    raise ValueError('Cannot reuse changed episode target bytes')
            print(json.dumps(dict(day=entry['day'],status='verified_reused_sidecars')),flush=True)
            continue
        session=open_session(Path(entry['bank_root']),runtime_root=args.runtime_root,
                             previous_root=Path(entry['previous_root']))
        report=build_day(session,args.output/entry['day'])
        print(json.dumps(report),flush=True)


if __name__=='__main__':
    main()
