"""Exact sharing of market-only action and sizing graphs."""
import json
from .genome import MARKET_INPUTS,INPUTS,OPS

VERSION='market-only-all-heads-v4'


def independent(network):
    # Scan sizing and action nodes. Nonzero age bridges and reserved state
    # sources cannot enter the market-only engine.
    for node in network.nodes:
        if node.op not in OPS or (node.op=='decision' and node.bias!=0):return False
        if any(s<0 or MARKET_INPUTS<=s<INPUTS for s in node.sources):return False
    return True


def representatives(members):
    shared={};result=[]
    for i,member in enumerate(members):
        net=member.networks['entry']
        if not independent(net):result.append(i);continue
        key=json.dumps(net.payload(),sort_keys=True,separators=(',',':'))
        result.append(shared.setdefault(key,i))
    return result


def action_representatives(members):
    """Share only identical numerical graphs; genealogy is not computation."""
    from .genome import STAGES
    shared=[{} for _ in STAGES];result=[]
    for i,member in enumerate(members):
        row=[]
        for stage,table in zip(STAGES,shared):
            net=member.networks[stage]
            if not independent(net):raise ValueError('Stateful graph cannot enter market-only inference')
            payload=net.payload()
            payload['nodes']=[{k:v for k,v in n.items() if k not in ('uid','slot')} for n in payload['nodes']]
            row.append(table.setdefault(json.dumps(payload,sort_keys=True,separators=(',',':')),i))
        result.append(row)
    return result
