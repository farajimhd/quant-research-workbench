"""Content-addressed filtered successors of published V7 campaign histories."""
from copy import deepcopy
from functools import lru_cache
from datetime import date
import sys

from .derived_trade_policy import POLICY
from .historical_level_checkpoint import digest
from pipelines.market_sip.events.trade_reporting_flags import REVISION as REPORTING_REVISION

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
                reporting_revision=REPORTING_REVISION,
                reporting_coverage_contract='source-plan-v1',
                parent_plan_hash=parent['plan_hash'], derivation=VERSION,
                git_commit='content-addressed-source-files')
    plan['plan_hash'] = digest(plan)
    return root / VERSION / plan['plan_hash'], plan


def available_sources(root, ticker, candidates, session=None):
    """Publication is per ticker; never cache absence in a long-lived QMD worker."""
    from .v7_catalog import read
    from .v7_preparation_cache import watch
    result = []
    for _, parent, _ in candidates:
        if parent.get('input_policy') == POLICY:
            continue
        folder, expected = successor(root, parent, ticker)
        watch(folder / 'plan.json')
        if not (folder / 'plan.json').exists():
            continue
        plan = read(folder / 'plan.json')
        if plan != expected:
            raise ValueError('Filtered V7 successor plan differs from pinned authority')
        target = folder / 'tickers' / plan['rows'][0]['directory']
        for name in ('source-plan.json', 'ready.json', 'prefixes'):
            watch(target / name)
        if not (target / 'source-plan.json').exists():
            if (target / 'ready.json').exists() or any((target / 'prefixes').glob('*.json')):
                raise ValueError('Published filtered V7 source plan is missing')
            continue
        source = read(target / 'source-plan.json')
        if source['plan_hash'] != plan['plan_hash']:
            raise ValueError('Filtered V7 publication identity mismatch')
        if (not isinstance(source.get('reporting_coverage_hash'),str)
                or len(source['reporting_coverage_hash']) != 64):
            raise ValueError('Filtered V7 source lacks frozen trade-reporting coverage')
        days = [d['source_date'] for d in source['days']]
        if days != sorted(set(days)) or any(d['ticker'] != ticker for d in source['days']):
            raise ValueError('Filtered V7 source sessions must be unique and ticker-specific')
        if (target / 'ready.json').exists():
            ready = read(target / 'ready.json')
            if (ready['plan_hash'] != plan['plan_hash']
                    or ready.get('source_plan_hash') != digest(source)):
                raise ValueError('Filtered V7 publication identity mismatch')
        elif session:
            required = [d for d in days if d < session]
            if not required:
                continue
            publications = sorted((target / 'prefixes').glob('*.json'))
            covered = False
            for path in publications:
                value = read(path)
                if (value.get('prefix_hash') != digest({k:v for k,v in value.items() if k!='prefix_hash'})
                        or value.get('version') != 1 or value.get('plan_hash') != plan['plan_hash']
                        or value.get('ticker') != ticker or value.get('source_plan_hash') != digest(source)):
                    raise ValueError('Filtered V7 prefix integrity mismatch')
                if date.fromisoformat(value['before']).isoformat() != path.stem:
                    raise ValueError('Filtered V7 prefix boundary mismatch')
                prefix = [d for d in days if d < value['before']]
                if not prefix or value['sessions'] != len(prefix) or value['through'] != prefix[-1]:
                    raise ValueError('Filtered V7 prefix coverage mismatch')
                # Bind publication to its terminal receipt, including an empty suffix.
                from research.level_book.v7.campaign_source import source_hash
                receipt = read(target / 'receipts' / (prefix[-1] + '.json'))
                expected_hash = receipt.get('checkpoint_hash') if receipt['state'] == 'complete' else receipt.get('parent_hash')
                if (receipt['state'] not in ('complete', 'empty')
                        or receipt['source_hash'] != source_hash(source['days'][len(prefix)-1], plan['rules'])
                        or value['checkpoint_hash'] != expected_hash):
                    raise ValueError('Filtered V7 prefix terminal receipt mismatch')
                covered |= value['through'] >= required[-1]
            if not covered:
                continue
        else:
            continue
        result.append((target, plan, source))
    return result
