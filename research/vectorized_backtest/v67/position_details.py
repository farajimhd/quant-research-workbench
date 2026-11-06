"""Observer-only, on-demand extraction of a candidate's saved position ledger."""
import json
from pathlib import Path
from .progress_sessions import read_verified


def clock_timestamp(clocks,index,order_mode):
    """Decision indices and terminal elapsed-second indices share one origin."""
    if int(index)!=index or index<0:raise ValueError('Invalid position clock')
    index=int(index)
    if index<len(clocks):return int(clocks[index])
    if order_mode not in ('explicit-top5-whole-share-v2','explicit-top5-limit-v1','persistent-quote-size-orders-v2','strategy-managed-quote-orders-v3'):
        raise ValueError('Position clock exceeds replay')
    return int(clocks[0])+index


def shard_timeline(job,root,role,day,receipt):
    """Resolve the V67 shared-shard clock and source-symbol identity contract."""
    import numpy as np
    from .distributed_assets import remote
    from .shard_data import listing_records
    from research.market_shards.v1.contract import CONTRACT,origin_us
    def path(value):
        return remote(value,job['workstation_share']) if str(root).startswith('\\\\') and not str(value).startswith('\\\\') else Path(value)
    names=set();current=None
    for split in ('training','validation'):
        wrapper=read_verified(path(job[split]),job[split+'_sha256'])
        manifest=read_verified(path(wrapper['shard_manifest']),wrapper['shard_manifest_sha256'])
        names.update(t for row in manifest['sessions'] for t in row['selected'])
        if split==role:
            if day not in wrapper['days']:raise ValueError('Position session outside split')
            read_verified(path(wrapper['inputs'])/day/'complete.json',wrapper['receipts'][day])
            current=wrapper
    if receipt['result_identity']['data']['input_sha256']!=current['shard_manifest_sha256']:
        raise ValueError('Position shared-shard identity changed')
    start,end=[sum(int(v)*m for v,m in zip(value.split(':'),(3600,60))) for value in CONTRACT['decision_hours']]
    return origin_us(day)//1000000+np.arange(start+1,end+1,dtype=np.int64),listing_records(names)


def load(root,generation,serial,session,share,job=None):
    from research.vectorized_backtest.v67.distributed_assets import remote
    role,day=session.split(':',1)
    folder=Path(root)/'global'/f'generation-{generation:04d}'
    seal=read_verified(folder/'complete.json') if (folder/'complete.json').exists() else None
    if role not in ('training','validation'):raise ValueError('Unknown position split')
    if seal:
        evidence=read_verified(folder/(role+'.json'),seal[role+'_sha256'])
    else:
        from .progress_sessions import population_metadata
        ids,token=population_metadata(folder/'population.json')
        from .completed_replays import reference
        path,expected=reference(job or {},Path(root)/'replays'/role,role,day,generation)
        evidence=dict(ids=ids,population_sha256=token,receipts={day:dict(receipt=str(path),receipt_sha256=expected)})
    index=evidence['ids'].index(serial)
    reference=evidence['receipts'][day];path=Path(reference['receipt'])
    if str(root).startswith('\\\\') and not str(path).startswith('\\\\'):path=remote(path,share)
    receipt=read_verified(path,reference['receipt_sha256'])
    if receipt['population_sha256']!=evidence['population_sha256'] or receipt['day']!=day or receipt['split']!=role:
        raise ValueError('Position receipt identity mismatch')
    if not seal and (not receipt['full_session'] or receipt['validation_opened']!=(role=='validation') or (job and receipt['execution']!=job['execution'])):
        raise ValueError('Unsealed position receipt authority mismatch')
    if any(len(values)!=len(evidence['ids']) for values in receipt['metrics'].values()):raise ValueError('Position candidate alignment mismatch')
    metrics=receipt['metrics'];profits=metrics['episode_profits'][index]
    fields={k:v[index] for k,v in metrics.items() if k.startswith('episode_')}
    if any(len(v)!=len(profits) for v in fields.values()):raise ValueError('Position trace alignment mismatch')
    rows=[]
    for i,profit in enumerate(profits):
        row={k.removeprefix('episode_'):v[i] for k,v in fields.items()}
        entry=row.get('entry_clocks');exit=row.get('exit_clocks');listing=row.get('listings')
        row.update(position=i+1,net_pnl=profit,status='closed',listing_id=receipt['listing_ids'][int(listing)],
                   duration=exit-entry if entry is not None and exit is not None else None)
        rows.append(row)
    for listing,profit in zip(metrics.get('open_episode_listings',[[]]*len(evidence['ids']))[index],metrics.get('open_episode_realized',[[]]*len(evidence['ids']))[index]):
        rows.append(dict(position=len(rows)+1,status='open',listing_id=receipt['listing_ids'][int(listing)],net_pnl=profit))
    for row in rows:row['evidence']='sealed generation' if seal else 'published replay; generation unsealed'
    from .order_details import attach
    attach(rows,receipt,index,lambda p:remote(p,share) if str(root).startswith('\\\\') and not str(p).startswith('\\\\') else p)
    if job:
        import numpy as np
        from datetime import datetime,timezone
        manifest=read_verified(job[role],job[role+'_sha256'])
        inputs=Path(manifest['inputs'])/day
        if str(root).startswith('\\\\'):inputs=remote(inputs,share)
        if manifest.get('version')=='v67-arte-manifest-v1':
            clocks,listings=shard_timeline(job,root,role,day,receipt)
        else:
            cert=read_verified(inputs/'complete.json',manifest['receipts'][day]['inputs'])
            from research.vectorized_backtest.v67.distributed_assets import sha
            if sha(inputs/'clocks.npy')!=cert['files']['clocks.npy']:raise ValueError('Position timeline changed')
            clocks=np.load(inputs/'clocks.npy',mmap_mode='r',allow_pickle=False);listings=cert['listings']
        def timestamp(index):
            return clock_timestamp(clocks,index,receipt['execution'].get('order_mode'))
        for row in rows:
            listing=listings[row['listing_id']]
            row.update(ticker=listing['ticker'],listing_identity=listing['listing_id'])
            for prefix in ('entry','exit'):
                index=row.get(prefix+'_clocks')
                if index is not None:row[prefix+'_utc']=datetime.fromtimestamp(timestamp(index),timezone.utc).isoformat()
            if row.get('entry_clocks') is not None and row.get('exit_clocks') is not None:
                row['duration_seconds']=timestamp(row['exit_clocks'])-timestamp(row['entry_clocks'])
    return rows
