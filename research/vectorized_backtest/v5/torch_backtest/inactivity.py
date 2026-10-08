"""Elapsed UTC inactivity from bound actual entry fills; no market reads."""
from datetime import datetime
from pathlib import Path
import torch

def area(seconds):
    if seconds < 0: raise ValueError('Negative inactivity duration')
    return sum(min(1., hour/5.) * max(0., min(3600., seconds-hour*3600.)) for hour in range(1,5)) + max(0., seconds-18000.)

def panel_metrics(receipt, folder):
    session=receipt['execution']['creator_identity']['session']
    start,end=[datetime.fromisoformat(session[k]).timestamp() for k in ('start','end')]
    if end <= start: raise ValueError('Invalid eligible session duration')
    values=[]
    for batch in receipt['batch_receipts']:
        saved=torch.load(Path(folder)/batch['directory']/'fills.pt',map_location='cpu',weights_only=True)
        for lane,count in enumerate(saved['counts'].tolist()):
            stamps=saved['ledger'][lane,:count]
            # Buy fills are entry/add fills; sells never reset inactivity.
            entries=sorted(set(stamps[stamps[:,3]==1,0].tolist()))
            if any(t<start or t>end for t in entries): raise ValueError('Entry outside certified session')
            boundaries=[start]+entries+[end]
            values.append(sum(area(b-a) for a,b in zip(boundaries,boundaries[1:])))
    order=receipt.get('candidate_order',list(range(len(values))))
    if sorted(order)!=list(range(len(values))): raise ValueError('Invalid inactivity lane coverage')
    restored=[0.]*len(values)
    for i,candidate in enumerate(order): restored[candidate]=values[i]
    return dict(receipt['metrics'],inactivity_seconds=restored,eligible_seconds=[end-start]*len(values))
