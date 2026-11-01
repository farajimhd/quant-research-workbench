"""Reconciled strategy admission counters, separate from financial selection."""
NAMES=('entry_signals','entry_requests','abstain_held','abstain_pending',
       'abstain_terminal','abstain_cancelled','abstain_membership','abstain_quote',
       'abstain_stop','abstain_cooldown','abstain_size','abstain_other',
       'standing_limit_wait','standing_liquidity_wait','entry_first_fills','cancelled_orders')


def accumulate(target,raw,accepted,reasons,limit_wait,liquidity_wait,entry_fill,cancelled,active=True):
    import torch
    remaining=raw&~accepted;fields=[raw,accepted]
    for reason in reasons:
        fields.append(remaining&reason);remaining=remaining&~reason
    fields.extend((remaining,limit_wait,liquidity_wait,entry_fill,cancelled))
    values=torch.stack(fields,-1).to(torch.int64)*active
    u=values.shape[-2];tiles=target.shape[1]
    values=torch.nn.functional.pad(values,(0,0,0,tiles*128-u))
    target.add_(values.reshape(target.shape[0],tiles,128,len(NAMES)).sum(2))


def export(counters):
    values=counters.sum(1).cpu().numpy()
    if (values[:,0]!=values[:,1]+values[:,2:12].sum(1)).any():
        raise RuntimeError('Admission counters do not reconcile; no receipt published')
    return {'admission_'+name:values[:,i].tolist() for i,name in enumerate(NAMES)}
