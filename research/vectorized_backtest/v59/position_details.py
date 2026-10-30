"""Observer-only, on-demand extraction of a candidate's saved position ledger."""
import json
from pathlib import Path
from .progress_sessions import read_verified


def load(root,generation,serial,session,share,job=None):
    from research.vectorized_backtest.v42.distributed_assets import remote
    role,day=session.split(':',1)
    folder=Path(root)/'global'/f'generation-{generation:04d}'
    seal=read_verified(folder/'complete.json') if (folder/'complete.json').exists() else None
    if role not in ('training','validation'):raise ValueError('Unknown position split')
    if seal:
        evidence=read_verified(folder/(role+'.json'),seal[role+'_sha256'])
    else:
        from .progress_sessions import population_metadata
        ids,token=population_metadata(folder/'population.json')
        path=Path(root)/'replays'/role/f'generation-{generation:04d}'/day/'receipt.json'
        evidence=dict(ids=ids,population_sha256=token,receipts={day:dict(receipt=str(path),receipt_sha256=None)})
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
    if job:
        import numpy as np
        from datetime import datetime,timezone
        manifest=read_verified(job[role],job[role+'_sha256'])
        inputs=Path(manifest['inputs'])/day
        if str(root).startswith('\\\\'):inputs=remote(inputs,share)
        cert=read_verified(inputs/'complete.json',manifest['receipts'][day]['inputs'])
        from research.vectorized_backtest.v42.distributed_assets import sha
        if sha(inputs/'clocks.npy')!=cert['files']['clocks.npy']:raise ValueError('Position timeline changed')
        clocks=np.load(inputs/'clocks.npy',mmap_mode='r',allow_pickle=False)
        def timestamp(index):
            if index<len(clocks):return int(clocks[index])
            if receipt['execution'].get('order_mode')!='persistent-quote-size-orders-v2':raise ValueError('Position clock exceeds replay')
            return int(clocks[0])+int(index)
        for row in rows:
            listing=cert['listings'][row['listing_id']]
            row.update(ticker=listing['ticker'],listing_identity=listing['listing_id'])
            for prefix in ('entry','exit'):
                index=row.get(prefix+'_clocks')
                if index is not None:row[prefix+'_utc']=datetime.fromtimestamp(timestamp(index),timezone.utc).isoformat()
            if row.get('entry_clocks') is not None and row.get('exit_clocks') is not None:
                row['duration_seconds']=timestamp(row['exit_clocks'])-timestamp(row['entry_clocks'])
    return rows
