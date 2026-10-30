"""Phase-0 financial ownership, independent of the causal feature cross-section."""
from dataclasses import replace
import json
from pathlib import Path
import numpy as np
import torch


def inventory(config, day):
    receipt=json.loads((Path(config['inputs'])/day/'complete.json').read_bytes())
    history=json.loads((Path(config['history'])/day/'complete.json').read_bytes())
    listings=[receipt['listings'][int(i)] for i in history['listing_ids']]
    return sorted({day+':'+x['security_id'] for x in listings})


def assign_initial(members,cases):
    if not cases or any(m.case_owner is not None for m in members):raise ValueError('Fresh unowned initial cohort required')
    return [replace(m,case_owner=cases[i%len(cases)]).validate() for i,m in enumerate(members)]


def action_mask(item,members,width):
    owners=[m.case_owner for m in members]
    if not any(x is not None for x in owners):return None
    if any(x is None for x in owners):raise ValueError('Mixed owned/unowned replay is forbidden')
    day=item.root.name
    cases=[day+':'+item.receipt['listings'][int(i)]['security_id'] for i in item.listing_ids]
    if not set(owners)<=set(cases):raise ValueError('Unknown replay case owner')
    allowed=np.asarray([[o==c for c in cases] for o in owners],bool)
    allowed=np.pad(allowed,((0,0),(0,width-len(cases))),constant_values=False)
    # Only entry is disabled outside the owned case; existing held positions
    # cannot exist there, and exit/risk protection must never be disabled.
    return torch.tensor(np.where(allowed,31,30)[None],dtype=torch.int16,device=item.device)


def bind_neural_lanes(plan,item,members):
    """Skip neural work in permanently nontradable phase0 lanes only.

    Projection/normalization still reads the full causal cross-section. Recurrent
    state is listing-local; no skipped lane can hold a position or feed an owner.
    """
    import os
    plan.owner_indices=None
    if os.environ.get('V59_OWNER_LANES','1')=='0' or not any(m.case_owner is not None for m in members):return
    if any(m.case_owner is None for m in members):raise ValueError('Mixed neural lane ownership')
    cases=[item.root.name+':'+item.receipt['listings'][int(i)]['security_id'] for i in item.listing_ids]
    lookup={case:[i for i,c in enumerate(cases) if c==case] for case in set(cases)}
    indexes=[lookup[m.case_owner] for m in members];width=max(map(len,indexes))
    packed=np.full((len(members),width),-1,np.int32)
    for i,rows in enumerate(indexes):packed[i,:len(rows)]=rows
    plan.owner_indices=torch.as_tensor(packed,device=item.device)


def audit(members,metrics,mapping):
    cases=[mapping['day']+':'+s for s in mapping['security_ids']]
    listing=dict(zip(mapping['listing_ids'],cases))
    owners=[m.case_owner for m in members]
    if not any(x is not None for x in owners):return
    if any(x is None for x in owners):raise ValueError('Incomplete ownership ledger')
    for i,owner in enumerate(owners):
        if owner not in cases:raise ValueError('Unknown case owner')
        indexes=metrics['episode_listings'][i]+metrics.get('open_episode_listings',[[]]*len(members))[i]
        if any(j<0 or j>=len(cases) or cases[j]!=owner for j in indexes):raise ValueError('Cross-case position leakage')
        if any(listing[k]!=owner and abs(v)>1e-9 for k,v in metrics['ticker_pnl'][i].items()):raise ValueError('Cross-case cashflow leakage')


def quotas(cases,positive,target,generation):
    if not 1<=len(cases)<=target:raise ValueError('Case inventory exceeds bounded population')
    out={c:1 for c in cases}  # Every case retains fresh exploration access.
    pool=sorted(positive) or sorted(cases)
    offset=(generation-1)%len(pool);pool=pool[offset:]+pool[:offset]
    for i in range(target-len(cases)):out[pool[i%len(pool)]]+=1
    return out


def round_robin(groups,generation):
    cases=sorted(groups)
    if not cases:return []
    offset=(generation-1)%len(cases);cases=cases[offset:]+cases[:offset]
    return [groups[c][rank] for rank in range(max(map(len,groups.values()))) for c in cases if rank<len(groups[c])]
