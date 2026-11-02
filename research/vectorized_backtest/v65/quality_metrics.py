"""Observer-only population statistics. Never imported by optimization code."""
import hashlib
import numpy as np


def finite(value):
    return float(value) if value is not None and np.isfinite(value) else None


def distribution(values):
    x=np.asarray(values,float);x=x[np.isfinite(x)]
    return dict(median=finite(np.median(x)) if len(x) else None,
                p90=finite(np.quantile(x,.9)) if len(x) else None,
                best=finite(x.max()) if len(x) else None)


def extract(metrics,fees=None):
    """Discard large position ledgers after deriving reusable compact columns."""
    from .full_common import aggregate
    p=aggregate([metrics]);n=len(p['net_pnl'])
    result={k:np.asarray(v) for k,v in p.items() if np.asarray(v).shape==(n,)}
    result['fees']=np.full(n,np.nan) if fees is None else np.asarray(fees,float)
    positive=[];largest=[];holding=[];patterns=[]
    for i in range(n):
        profits=np.asarray(metrics['episode_profits'][i],float)
        listings=np.asarray(metrics['episode_listings'][i],np.int64)
        opened=np.asarray(metrics.get('open_episode_realized',[[]]*n)[i],float)
        if len(opened):
            profits=np.r_[profits,opened]
            listings=np.r_[listings,metrics['open_episode_listings'][i]]
        _,inverse=np.unique(listings,return_inverse=True)
        ticker=np.bincount(inverse,weights=profits);wins=ticker[ticker>0]
        positive.append(wins.sum());largest.append(wins.max() if len(wins) else 0.)
        duration=np.asarray(metrics['episode_exit_clocks'][i],np.int64)-np.asarray(metrics['episode_entry_clocks'][i],np.int64)
        if (duration<0).any():raise ValueError('Negative holding duration in receipt')
        holding.append(duration.astype(np.int32))
        h=hashlib.sha256()
        for field in ('episode_listings','episode_entry_clocks','episode_exit_clocks','episode_profits'):
            h.update(np.asarray(metrics[field][i],dtype=np.float64).tobytes())
        patterns.append(h.hexdigest())
    result.update(ticker_positive=np.asarray(positive),ticker_largest=np.asarray(largest),
        behavior=np.asarray(patterns),holding_offsets=np.cumsum(np.r_[0,[len(x) for x in holding]]),
        holding_values=np.concatenate(holding) if holding else np.array([],np.int32))
    return result


def combine(sessions):
    if not sessions:return {}
    scalar=('net_pnl','score','gross_losses','drawdown','closed_positions','winning_positions','fees','ticker_positive')
    out={k:np.sum([s[k] for s in sessions],axis=0) for k in scalar}
    net=np.asarray([s['net_pnl'] for s in sessions]);trades=np.asarray([s.get('admission_entry_first_fills',s['closed_positions']) for s in sessions])
    out.update(sessions_traded=(trades>0).sum(0),profitable_sessions=(net>0).sum(0),
        worst_session=net.min(0),worst_drawdown=np.max([s['drawdown'] for s in sessions],axis=0),
        top_day_share=np.divide(np.maximum(net,0).max(0),np.maximum(net,0).sum(0),out=np.full(net.shape[1],np.nan),where=np.maximum(net,0).sum(0)>0),
        top_ticker_share=np.divide(np.max([s['ticker_largest'] for s in sessions],axis=0),out['ticker_positive'],out=np.full(net.shape[1],np.nan),where=out['ticker_positive']>0))
    for k in sessions[0]:
        if k.startswith('admission_'):out[k]=np.sum([s[k] for s in sessions],axis=0)
    out['before_fees']=out['net_pnl']+out['fees']
    out['win_rate_pct']=np.divide(100*out['winning_positions'],out['closed_positions'],out=np.full(net.shape[1],np.nan),where=out['closed_positions']>0)
    out['behavior']=np.asarray([hashlib.sha256(''.join(s['behavior'][i] for s in sessions).encode()).hexdigest() for i in range(net.shape[1])])
    return out


def summary(p,indices,records,keys,sessions,coverage,total,parent_delta=None,next_ids=None):
    idx=np.asarray(indices,int);n=len(idx)
    if not n:return dict(count=0,coverage=coverage,total=total)
    select=lambda k:np.asarray(p[k])[idx]
    count=lambda x:int(np.count_nonzero(x))
    trades=select('admission_entry_first_fills') if 'admission_entry_first_fills' in p else select('closed_positions');inactive=trades==0
    signals=select('admission_entry_signals') if 'admission_entry_signals' in p else None
    requests=select('admission_entry_requests') if 'admission_entry_requests' in p else None
    rows=[records[i] for i in idx];nodes=[r.get('node_count',np.nan) for r in rows]
    # Complexity metadata varies by producer; unavailable is never reported as zero.
    edges=[r['complexity']-r['node_count'] if isinstance(r.get('complexity'),(int,float)) and r.get('node_count') is not None else np.nan for r in rows]
    durations=[s['holding_values'][s['holding_offsets'][i]:s['holding_offsets'][i+1]] for s in sessions for i in idx]
    holding=np.concatenate(durations) if durations else np.array([])
    delta=np.asarray(parent_delta)[idx] if parent_delta is not None else np.full(n,np.nan)
    comparisons=delta[np.isfinite(delta)]
    from collections import Counter
    parents=Counter(parent for r in rows for parent in set(r.get('parents',[])) if parent>=0)
    out=dict(count=n,coverage=coverage,total=total,complete=coverage==total,
        inactive=count(inactive),inactive_pct=100*count(inactive)/n,
        no_signal=count(inactive&(signals==0)) if signals is not None else None,
        blocked=count(inactive&(signals>0)&(requests==0)) if requests is not None and signals is not None else None,
        unfilled=count(inactive&(requests>0)) if requests is not None else None,
        profitable_pct=100*count(select('net_pnl')>0)/n,positive_score_pct=100*count(select('score')>0)/n,
        parent_comparisons=len(comparisons),beat_parent_pct=100*count(comparisons>0)/len(comparisons) if len(comparisons) else None,
        parent_improvement=finite(np.median(comparisons)) if len(comparisons) else None,
        retained=sum(r['serial'] in next_ids for r in rows) if next_ids is not None else None,
        unique_genomes=len(set(keys[i] for i in idx)),distinct_behaviors=len(set(select('behavior'))),
        duplicate_genomes=n-len(set(keys[i] for i in idx)),
        largest_parent_lineage_pct=100*max(parents.values())/n if parents else None,
        median_holding_seconds=finite(np.median(holding)) if len(holding) else None,
        active_nodes=distribution(np.asarray(nodes)[~inactive]),active_edges=distribution(np.asarray(edges)[~inactive]),
        traded_sessions_histogram={str(int(v)):int(c) for v,c in zip(*np.unique(select('sessions_traded'),return_counts=True))},
        profitable_sessions_histogram={str(int(v)):int(c) for v,c in zip(*np.unique(select('profitable_sessions'),return_counts=True))})
    for k in ('score','net_pnl','drawdown','gross_losses','fees','before_fees','closed_positions','win_rate_pct','sessions_traded','profitable_sessions','top_day_share','top_ticker_share','worst_session','worst_drawdown'):
        out[k]=distribution(select(k))
    return out


def generalization(train,valid,ids,train_days,valid_days):
    """Same-identity comparison; leaders are selected exclusively on training."""
    if not train or not valid:return dict(available=False)
    from scipy.stats import spearmanr
    score_t=train['score']/train_days;score_v=valid['score']/valid_days
    net_t=train['net_pnl']/train_days;net_v=valid['net_pnl']/valid_days
    positive=net_t>0
    corr=finite(spearmanr(score_t,score_v).statistic) if np.ptp(score_t)>0 and np.ptp(score_v)>0 else None
    order=np.lexsort((np.asarray(ids),-score_t))
    inactive=valid.get('admission_entry_first_fills',valid['closed_positions'])==0
    result=dict(available=True,rank_correlation=corr,validation_inactive_pct=100*float(np.mean(inactive)),
        positive_to_negative_pct=100*float(np.mean(net_v[positive]<0)) if positive.any() else None,
        positive_training_candidates=int(positive.sum()),leaders=[])
    for top in (1,10,100):
        idx=order[:top]
        result['leaders'].append(dict(top=top,count=len(idx),train_net_per_session=float(np.median(net_t[idx])),
            validation_net_per_session=float(np.median(net_v[idx])),train_score_per_session=float(np.median(score_t[idx])),
            validation_score_per_session=float(np.median(score_v[idx])),train_profitable_session_pct=float(np.median(train['profitable_sessions'][idx]/train_days*100)),
            validation_profitable_session_pct=float(np.median(valid['profitable_sessions'][idx]/valid_days*100)),
            validation_worst_session=float(valid['worst_session'][idx].min()),validation_drawdown=float(np.median(valid['drawdown'][idx])),
            validation_top_day_share=distribution(valid['top_day_share'][idx])['median'],
            validation_inactive_pct=float(np.mean(inactive[idx])*100)))
    return result
