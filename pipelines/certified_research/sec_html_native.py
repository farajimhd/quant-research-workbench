"""Explicit issuer-scoped HTML measures; never aliases for standard concepts."""
import re
from .contracts import digest

PREFIX='urn:tradingml:sec-html-native:v1:cik:'


def definition(cik,label,statement_scope):
    cik=str(cik)
    if not re.fullmatch(r'0*[1-9][0-9]{0,9}',cik):raise ValueError('Invalid native issuer')
    if any(not isinstance(s,str) or not s or len(s)>4096 or s!=' '.join(s.split())
           for s in (label,statement_scope)):
        raise ValueError('Native labels require exact normalized source text')
    body=dict(issuer_cik=str(int(cik)).zfill(10),label=label,statement_scope=statement_scope)
    key=digest(body)
    return dict(body,definition_hash=key,concept='{'+PREFIX+body['issuer_cik']+'}N_'+key)


def verify(item,cik):
    native=item.get('native')
    if not isinstance(native,dict):raise ValueError('Native measure definition is missing')
    expected=definition(cik,item['label'],native.get('statement_scope'))
    if native!=expected or item['concept']!=expected['concept']:
        raise ValueError('Native measure identity or issuer differs')
    locator=item['locator']
    if locator['kind']=='cells':
        proof=item.get('table_proof',{})
        if proof.get('heading')!=native['statement_scope'] or not proof.get('headers'):
            raise ValueError('Native measure requires reviewed statement and column proof')
    elif locator['kind']=='quote':
        if native['statement_scope'] not in locator['quote']:
            raise ValueError('Native quote scope differs')
    else:raise ValueError('Native measure source locator differs')
    return native
