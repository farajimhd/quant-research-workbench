"""Sound entry-sharing eligibility, including implicit operator state reads."""
import json
from .genome import MARKET_INPUTS,INPUTS,OPS

VERSION='state-independent-entry-v2'


def independent(network):
    # Decision bridges read OPENED directly in both CPU and CUDA evaluators.
    # Even a zero-age bridge is conservatively kept individual. Scan all nodes:
    # validated graphs include the sizing head as well as the action head.
    for node in network.nodes:
        if node.op not in OPS or node.op=='decision':return False
        if any(s<0 or (MARKET_INPUTS<=s<INPUTS and s!=MARKET_INPUTS+7) for s in node.sources):return False
    return True


def representatives(members):
    shared={};result=[]
    for i,member in enumerate(members):
        net=member.networks['entry']
        if not independent(net):result.append(i);continue
        key=json.dumps(net.payload(),sort_keys=True,separators=(',',':'))
        result.append(shared.setdefault(key,i))
    return result
