"""Versioned admission masks for verified, superseded native semantic keys.

Raw facts and completed receipts remain immutable. Incorrect metadata is not
replayed as a valid feature, including in old knowledge windows. Replacement
facts keep their own recorded clock and the verified parent's public clock.
"""
from pipelines.certified_research.contracts import literal,identifier,digest


def retired_keys(ciks):
    from pipelines.certified_research.sec_html_additions import REGISTERED,entry_for
    keys=set()
    for scope,entry in REGISTERED.items():
        plan=entry['plan']
        if entry.get('mode')!='native_statement_scope_correction' or plan['cik'] not in ciks:continue
        entry_for(scope,plan['cik'],plan['document_hash'])
        keys.update((entry['parent_authority'],metric,context) for metric,context in entry['replaces_fact_keys'])
    return sorted(keys)


def policy_hash(ciks):
    return digest(retired_keys(ciks))


def exclusion(ciks,alias=''):
    prefix=identifier(alias)+'.' if alias else ''
    keys=retired_keys(ciks)
    if not keys:return ''
    wanted=','.join('('+','.join(map(literal,key))+')' for key in sorted(keys))
    return f' AND ({prefix}source_id,{prefix}metric,{prefix}context_id) NOT IN ({wanted})'
