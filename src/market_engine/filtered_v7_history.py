"""Content-addressed filtered successors of published V7 campaign histories."""
from copy import deepcopy
from functools import lru_cache
import sys

from .derived_trade_policy import POLICY
from .historical_level_checkpoint import digest

VERSION = 'filtered-v7-on-demand-v1'


@lru_cache(maxsize=1)
def kernel():
    import numpy
    import scipy
    from research.level_book.v7.campaign import hashes
    return dict(source_files=hashes(), software=dict(
        python=sys.version, numpy=numpy.__version__, scipy=scipy.__version__))


def successor(root, parent, ticker):
    """Same frozen identity/rules/full prefix, new policy and current fitter."""
    row = next(r for r in parent['rows'] if r['ticker'] == ticker)
    if row['status'] == 'deferred':
        raise ValueError('Cannot rebuild an unresolved V7 identity: '+ticker)
    plan = deepcopy({k:v for k,v in parent.items() if k not in ('rows','plan_hash')})
    plan.update(kernel(), rows=[deepcopy(row)], input_policy=POLICY,
                parent_plan_hash=parent['plan_hash'], derivation=VERSION,
                git_commit='content-addressed-source-files')
    plan['plan_hash'] = digest(plan)
    return root / VERSION / plan['plan_hash'], plan


def available_sources(root, ticker, candidates):
    """Publication is per ticker; never cache absence in a long-lived QMD worker."""
    from .level_book_store import read
    result = []
    for _, parent, _ in candidates:
        if parent.get('input_policy') == POLICY:
            continue
        folder, expected = successor(root, parent, ticker)
        if not (folder / 'plan.json').exists():
            continue
        plan = read(folder / 'plan.json')
        if plan != expected:
            raise ValueError('Filtered V7 successor plan differs from pinned authority')
        target = folder / 'tickers' / plan['rows'][0]['directory']
        # Only completely verified ticker histories become serving authority.
        if not (target / 'ready.json').exists():
            continue
        ready = read(target / 'ready.json')
        source = read(target / 'source-plan.json')
        if ready['plan_hash'] != plan['plan_hash'] or source['plan_hash'] != plan['plan_hash']:
            raise ValueError('Filtered V7 publication identity mismatch')
        days = [d['source_date'] for d in source['days']]
        if days != sorted(set(days)) or any(d['ticker'] != ticker for d in source['days']):
            raise ValueError('Filtered V7 source sessions must be unique and ticker-specific')
        result.append((target, plan, source))
    return result
