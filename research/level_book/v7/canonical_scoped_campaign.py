"""Certified development scope exporter for the unchanged filtered-prefix worker.

This prepares archive work, never financial admission. Full parent metadata is
verified even though checkpoint computation stops at the requested prefix.
"""
from contextlib import closing
from datetime import date, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from . import campaign as c, campaign_source as source, filtered_campaign as filtered
from src.market_engine.filtered_v7_history import kernel, successor
from src.data_provider.calendar import market_sessions

VERSION = 'canonical-v7-scoped-development-campaign@1'
SCOPE_KEYS = {'schema', 'role', 'sessions', 'configuration_number',
              'configuration_revision_id', 'configuration_payload_hash', 'exclusion_hash'}


def _hash(value):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{64}', value) or value == '0'*64:
        raise ValueError('Exact nonzero SHA256 required')


def read_scope(path, expected_hash, exclusion_path):
    _hash(expected_hash)
    raw = Path(path).read_bytes()
    if sha256(raw).hexdigest() != expected_hash:
        raise ValueError('Development scope bytes differ')
    scope = json.loads(raw)
    if (type(scope) is not dict or set(scope) != SCOPE_KEYS or scope['schema'] != VERSION
            or scope['role'] != 'development'):
        raise ValueError('Explicit development declaration required')
    for field in ('configuration_payload_hash', 'exclusion_hash'):
        _hash(scope[field])
    if (type(scope['configuration_number']) is not int or scope['configuration_number'] <= 0
            or type(scope['configuration_revision_id']) is not str or not scope['configuration_revision_id']):
        raise ValueError('Exact configuration identity required')
    days = scope['sessions']
    if type(days) is not list or not days or any(type(d) is not str for d in days):
        raise ValueError('Explicit ordered development sessions required')
    if days != sorted(set(days)) or any(date.fromisoformat(d).isoformat() != d for d in days):
        raise ValueError('Development session ordering differs')
    if any(date.fromisoformat(d) > date.today() for d in days):
        raise ValueError('Future development scope rejected')
    for day in days:
        if market_sessions(date.fromisoformat(day), date.fromisoformat(day)) != [date.fromisoformat(day)]:
            raise ValueError('Development target is not an exchange session')
    if sha256(Path(exclusion_path).read_bytes()).hexdigest() != scope['exclusion_hash']:
        raise ValueError('Dated exclusion bytes differ')
    if os.environ.get('BACKTEST_INPUT_EXCLUSIONS_FILE') != str(exclusion_path):
        raise ValueError('Explicit dated exclusion environment binding differs')
    return scope


def certified_scopes(reader, scope):
    """Verify full products before applying exclusions, never caller proof flags."""
    from src.backend.backtest_strategy_one_configuration import certify_numbered_configuration
    from src.backend.backtest_market_data import (certified_market_plan_from_arte,
        verify_market_day_plan, project_market_day_plan)
    from src.backend.backtest_liquidity_price import certify_price_level_plan
    from src.backend.backtest_input_scope import input_exclusions
    configured = certify_numbered_configuration(reader, scope['configuration_number'])
    if (configured.revision()['revision_id'] != scope['configuration_revision_id']
            or configured.payload_hash != scope['configuration_payload_hash']):
        raise ValueError('Installed sealed configuration differs')
    result = []
    for day in scope['sessions']:
        original = certified_market_plan_from_arte(sessions=(day,), tickers=(), configuration=configured.payload)
        verify_market_day_plan(original, reader)
        prices = certify_price_level_plan(original, reader)
        excluded = input_exclusions(day)
        retained = tuple(t for t in original.tickers if t not in excluded)
        if not retained:
            raise ValueError('Empty retained development population')
        scoped = project_market_day_plan(original, retained) if excluded else original
        scoped_prices = prices.projected(scoped)
        result.append(dict(target_session=day, tickers=list(scoped.tickers),
            original_market_token=original.token, scoped_market_token=scoped.token,
            original_price_token=prices.token, scoped_price_token=scoped_prices.token,
            exclusion_policy_hash=scope['exclusion_hash']))
    return result


def _query(reader, sql):
    return [json.loads(line) for line in reader.execute(sql+' FORMAT JSONEachRow').splitlines() if line]


def _parent(root, row, cache):
    """One fresh read per immutable parent path within this export only."""
    folder=Path(root)/row['parent']
    if folder not in cache:
        parent=c.read(folder/'plan.json')
        actual=c.digest({k:v for k,v in parent.items() if k!='plan_hash'})
        if parent.get('plan_hash')!=actual:
            raise ValueError('Frozen parent identity differs')
        cache[folder]=(parent,actual)
    parent,actual=cache[folder]
    if parent.get('plan_hash')!=row['parent_hash'] or actual!=row['parent_hash']:
        raise ValueError('Frozen parent identity differs')
    return parent


def prepare_manifest(root, scope, scopes, reader, *, canary_ticker=None, _parent_cache=None, _inventory=None):
    """Select the complete retained union and genuine chronological prefixes."""
    if [s['target_session'] for s in scopes] != scope['sessions']:
        raise ValueError('Certified dates differ from development declaration')
    inventory = filtered.make_plan(root) if _inventory is None else _inventory
    parent_cache={} if _parent_cache is None else _parent_cache
    by_ticker = {r['ticker']: r for r in inventory['rows']}
    union = sorted({t for s in scopes for t in s['tickers']})
    if not union:
        raise ValueError('Empty certified union')
    for ticker in union:
        if ticker not in by_ticker or by_ticker[ticker]['state'] != 'queued':
            raise ValueError('Retained ticker lacks resolved parent: '+ticker)
    if canary_ticker is not None and (type(canary_ticker) is not str or canary_ticker not in union):
        raise ValueError('Canary must belong to the complete certified retained union')
    selected_union = [canary_ticker] if canary_ticker is not None else union
    rows, proofs = [], []
    required_priors = {target: market_sessions(date.fromisoformat(target)-timedelta(days=14),
                         date.fromisoformat(target)-timedelta(days=1))[-1].isoformat()
                       for target in scope['sessions']}
    current_kernel = kernel()
    if current_kernel['source_files'] != c.hashes():
        raise ValueError('Cached numerical kernel differs from current source bytes')
    current_rules = _query(reader, c.RULE_SQL)
    reporting_cache = {}
    for ticker in selected_union:
        if ticker not in by_ticker or by_ticker[ticker]['state'] != 'queued':
            raise ValueError('Retained ticker lacks resolved parent: '+ticker)
        row = dict(by_ticker[ticker])
        parent = _parent(root,row,parent_cache)
        output, selected = successor(Path(root), parent, ticker)
        if (selected['plan_hash'] != row['plan_hash'] or str(output.relative_to(root)) != row['output']
                or selected['input_policy'] != c.POLICY or selected['reporting_revision'] != source.REPORTING_REVISION):
            raise ValueError('Current canonical successor policy differs')
        requested = [s['target_session'] for s in scopes if ticker in s['tickers']]
        before = max(requested)
        if parent['start'] > min(required_priors[d] for d in requested) or parent['end'] < max(required_priors[d] for d in requested):
            raise ValueError('Parent interval does not cover requested development prefix')
        parent_row = next(r for r in parent['rows'] if r['ticker'] == ticker)
        if _query(reader, source.coverage_sql(parent['start'], parent['end'], [ticker])) != [parent_row['coverage']]:
            raise ValueError('Frozen canonical source coverage differs')
        if current_rules != parent['rules']:
            raise ValueError('Canonical trade condition rules differ')
        days = _query(reader, 'SELECT * FROM market_sip_compact.events_ordinal_continuity FINAL WHERE '
            f'ticker={source.literal(ticker)} AND source_date BETWEEN {source.literal(parent["start"])} '
            f'AND {source.literal(parent["end"])} ORDER BY source_date')
        names = [d['source_date'] for d in days]
        if (not names or names != sorted(set(names)) or len(names) != parent_row['coverage']['days']
                or any(d['ticker'] != ticker for d in days)):
            raise ValueError('Canonical continuity is duplicate, missing or foreign')
        interval = (parent['start'], parent['end'])
        if interval not in reporting_cache:
            reporting_cache[interval] = _query(reader, source.reporting_coverage_sql(*interval))
        reporting = reporting_cache[interval]
        source.require_reporting_coverage(names, reporting)
        prefix = [d for d in names if d < before]
        for target in requested:
            previous = required_priors[target]
            if previous not in prefix:
                raise ValueError('Exact prior exchange source session missing: '+ticker+'/'+target)
        row.update(before=before, sessions=len(prefix))
        rows.append(row)
        proofs.append(dict(ticker=ticker, requested=requested, before=before, through=prefix[-1],
            source_sessions=prefix, full_metadata_start=parent['start'], full_metadata_end=parent['end'],
            continuity_hash=c.digest(days), reporting_coverage_hash=c.digest(reporting),
            parent_hash=row['parent_hash'], successor_hash=row['plan_hash']))
    rows.sort(key=lambda r:(-r['sessions'], r['ticker']))
    proof = dict(schema=VERSION, declaration=scope, scopes=scopes, units=proofs,
        retained_parent_inventory=[by_ticker[t] for t in union],
        selection=dict(mode='canary' if canary_ticker is not None else 'full', ticker=canary_ticker),
        kernel=current_kernel, scheduler_hashes=filtered.execution_hashes(),
        exporter_sources={p:sha256((c.REPO/p).read_bytes()).hexdigest() for p in
            ('research/level_book/v7/canonical_scoped_campaign.py', 'scripts/prepare_canonical_v7_scoped_campaign.py',
             'research/level_book/v7/canonical_source_reader.py', 'scripts/clickhouse/provision_canonical_v7_source_reader.py')},
        catalog_hash=inventory['catalog_hash'])
    proof['scope_proof_hash'] = c.digest(proof)
    plan = dict(version=1, catalog_hash=inventory['catalog_hash'], consumer_kernel=current_kernel,
        scheduler_hashes=filtered.execution_hashes(), rows=rows, scoped_authority=proof)
    plan['manifest_hash'] = c.digest(plan)
    return plan


def build_manifest(root, scope_path, scope_hash, exclusion_path, *, canary_ticker=None):
    scope = read_scope(scope_path, scope_hash, exclusion_path)
    from src.backend.backtest_market_data import readonly_clickhouse_client
    with closing(readonly_clickhouse_client(v3_read_principal=True)) as reader:
        scopes = certified_scopes(reader, scope)
    inventory=filtered.make_plan(Path(root))
    parents={r['ticker']:r for r in inventory['rows']}
    parent_cache={}
    years=set()
    for ticker in sorted({t for s in scopes for t in s['tickers']}):
        row=parents.get(ticker)
        if row is None or row['state']!='queued':
            raise ValueError('Retained ticker lacks resolved parent: '+ticker)
        parent=_parent(root,row,parent_cache)
        years.update(range(date.fromisoformat(parent['start']).year,date.fromisoformat(parent['end']).year+1))
    from .canonical_source_reader import SourceReadPlan, source_client, POLICY_KEY
    source_plan=SourceReadPlan(tuple(sorted(years)),os.environ.get(POLICY_KEY,''))
    with closing(source_client(source_plan)) as reader:
        plan=prepare_manifest(Path(root),scope,scopes,reader,canary_ticker=canary_ticker,
                              _parent_cache=parent_cache,_inventory=inventory)
    plan['scoped_authority']['source_read_contract']=source_plan.payload()
    proof=plan['scoped_authority']
    proof['scope_proof_hash']=c.digest({k:v for k,v in proof.items() if k!='scope_proof_hash'})
    plan['manifest_hash']=c.digest({k:v for k,v in plan.items() if k!='manifest_hash'})
    return plan


def export_manifest(root, output, scope_path, scope_hash, exclusion_path, *, canary_ticker=None):
    plan = build_manifest(root, scope_path, scope_hash, exclusion_path, canary_ticker=canary_ticker)
    # One atomic immutable artifact: no partially visible companion proof.
    with c.exclusive(Path(output)/'scope-export.lock'):
        c.write(Path(output)/'plan.json', plan)
    return plan


def verify_export(root, output, scope_path, scope_hash, exclusion_path, *, canary_ticker=None):
    """Fresh source proof before the existing producer scheduler is permitted."""
    existing = filtered.checked_plan(Path(output))
    fresh = build_manifest(root, scope_path, scope_hash, exclusion_path, canary_ticker=canary_ticker)
    if existing != fresh:
        raise ValueError('Scoped campaign changed; export a new immutable manifest')
    return fresh
