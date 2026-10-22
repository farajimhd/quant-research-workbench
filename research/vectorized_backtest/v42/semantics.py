from collections import Counter

from dataclasses import asdict

REJECTIONS=Counter()

class SemanticError(ValueError):pass

def identity(member):

    payload=member.payload()

    for net in payload['networks'].values():

        for node in net['nodes']:node.pop('uid',None);node.pop('slot',None)

    return payload

def snapshot():return dict(REJECTIONS)

