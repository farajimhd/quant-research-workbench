"""Small, allowlisted generation summaries. No SDK or optimizer imports."""
import hashlib
import json
import math
import statistics
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def packet(campaign, generation):
    """None until both splits and the complete generation wall receipt exist."""
    base=Path(campaign);name=f'generation-{generation:04d}'
    train=base/'training'/name;valid=base/'validation'/name
    paths=[train/'complete.json',valid/'complete.json',train/'wall.json']
    if not all(p.exists() for p in paths):return None
    training,validation,wall=map(read,paths)
    if training.get('validation_feedback') is not False or validation.get('validation_feedback') is not False or validation.get('display_only') is not True:
        raise ValueError('Display-only split seals required')
    rows=read(train/'ranking.json');observations=read(valid/'ranking.json')
    observed_ids={r['serial'] for r in observations}
    leader=min((r for r in rows if r['serial'] in observed_ids),key=lambda r:r['rank'])
    by_serial={r['serial']:r for r in rows}
    if len({r['serial'] for r in observations})!=len(observations):raise ValueError('Duplicate validation candidate')
    for row in observations:
        original=by_serial[row['serial']]
        if row['candidate_id']!=original['candidate_id'] or row['training_rank']!=original['rank']:
            raise ValueError('Training/validation candidate identity mismatch')
    matched=[r for r in observations if r['serial']==leader['serial']]
    if len(matched)!=1:raise ValueError('Training leader missing from validation')
    values={'generation':generation,'leader/id':leader['candidate_id'],
            'leader/complexity':leader['metrics']['complexity']}
    for split,seal,chosen,shortlist in (
        ('train',training,leader,[by_serial[r['serial']] for r in observations]),
        ('validation',validation,matched[0],observations)):
        days=len(seal['session_receipts'])
        if not days:raise ValueError('Empty session panel')
        m=chosen['metrics']
        metrics=dict(score=chosen['score'],net_profit_per_session=m['total_pnl']/days,
            trimmed_profit_per_session=m['trimmed_profit']/days,trade_tail_loss=m['trade_tail_loss'],
            session_tail_mean_pnl=m['session_tail_mean_pnl'],worst_session_drawdown=m['worst_drawdown'],
            profitable_session_rate=m['profitable_day_fraction'],active_session_rate=m['active_sessions']/days,
            positions_per_session=m['closed_positions']/days,session_count=days,
            shortlist_median_trimmed_per_session=statistics.median(r['metrics']['trimmed_profit']/days for r in shortlist),
            result_cache_reuse_rate=sum(r['metrics'].get('reused_sessions',0) for r in (rows if split=='train' else observations))/(days*len(rows if split=='train' else observations)))
        for key in ('global_session_trim_removed','local_ticker_trim_removed','local_other_session_loss_penalty','best_day_gross_positive_share'):
            metrics[key]=m.get(key,0.)
        values.update({f'{split}/{key}':value for key,value in metrics.items()})
    values['time/generation_seconds']=wall['full_generation_seconds']
    values['time/validation_seconds']=wall['validation_seconds']
    diversity=read(train/'diversity.json')
    quality=train/'rule-quality.json'
    if quality.exists():values.update({'selection/rejected_'+k:v for k,v in read(quality).items()})
    attempts=diversity.get('crossovers',0)+diversity.get('unrelated_mate_fallbacks',0)
    if attempts:values['selection/unrelated_mate_fallback_rate']=diversity['unrelated_mate_fallbacks']/attempts
    if any(not math.isfinite(v) for k,v in values.items() if k!='leader/id'):
        raise ValueError('Nonfinite summary metric')
    return values


def checksum(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
