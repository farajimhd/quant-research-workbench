"""Canonical-only metadata parents for exact gaps in certified DEV inventories.

A parent attests market ticker/source chronology, not broker or fundamental
identity. It contains no fitted books and never certifies financial readiness.
"""
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
import re

from . import campaign as c, campaign_source as source
from src.backend.swing_book_source import HISTORICAL_POLICY
from src.backend.swing_book_indexed_source import ordinal_bounds
from src.data_provider.calendar import market_sessions
from src.market_engine.derived_trade_policy import POLICY
from src.market_engine.filtered_v7_history import kernel, successor as legacy_successor
from src.market_engine.reaction_band import CONFIG
from src.market_engine.streaming_level_book import EXTRACTION_VERSION

VERSION = 'canonical-v7-certified-metadata-parent@1'
DERIVATION = 'canonical-v7-certified-metadata-successor@1'
SUCCESSOR_DIRECTORY = 'canonical-metadata-successors-v1'
PARENT_DIRECTORY = 'canonical-metadata-parents-v1'
SOURCE_FILES = ('research/level_book/v7/canonical_metadata_parent.py',
                'scripts/prepare_canonical_v7_metadata_parent.py',
                'research/level_book/v7/canonical_scoped_campaign.py',
                'research/level_book/v7/filtered_campaign.py',
                'research/level_book/v7/canonical_archive_publication.py',
                'research/level_book/v7/canonical_source_reader.py',
                'scripts/clickhouse/provision_canonical_v7_source_reader.py')
KEYS = {'version','start','end','source_policy','input_policy','reporting_revision',
        'reporting_coverage_contract','extraction_version','band_config','rules','rows',
        'declaration','scopes','base_inventory','base_inventory_hash','source_files',
        'software','metadata_parent_sources','population_contract','plan_hash'}
ROW_KEYS = {'ticker','directory','status','reason','coverage','source_days',
            'continuity_hash','reporting_rows','reporting_hash','requested_targets'}


def sources():
    return {p:sha256((c.REPO/p).read_bytes()).hexdigest() for p in SOURCE_FILES}


def _same(a, b):
    if type(a) is not type(b):
        return False
    if type(a) is dict:
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if type(a) in (tuple,list):
        return len(a) == len(b) and all(_same(x,y) for x,y in zip(a,b))
    return a == b


def _day(value):
    if type(value) is not str or date.fromisoformat(value).isoformat() != value:
        raise ValueError('Canonical metadata date required')
    return date.fromisoformat(value)


def _hash(value):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{64}',value) or value == '0'*64:
        raise ValueError('Exact metadata identity required')


def relative(parent_hash):
    _hash(parent_hash)
    return PARENT_DIRECTORY+'/'+parent_hash


def is_relative(value):
    return (type(value) is str and re.fullmatch(re.escape(PARENT_DIRECTORY)+r'/[0-9a-f]{64}',value) is not None
            and value.rsplit('/',1)[1] != '0'*64)


def _inventory(root):
    from .filtered_campaign import make_plan
    return make_plan(root)


def source_interval(root, scopes, inventory):
    """Retain the frozen catalog prefix, through the last declared DEV prior."""
    from src.market_engine.v7_catalog import CAMPAIGNS
    starts = []
    seen = set()
    for row in inventory['rows']:
        if row['state'] != 'queued' or row['parent'] in seen:
            continue
        seen.add(row['parent'])
        parent_relative = catalog_relative(row['parent'])
        if parent_relative not in CAMPAIGNS:
            raise ValueError('Base parent is outside installed catalog')
        parent=c.read(Path(root)/parent_relative/'plan.json')
        if (parent['plan_hash'] != row['parent_hash']
                or parent['plan_hash'] != c.digest({k:v for k,v in parent.items() if k!='plan_hash'})):
            raise ValueError('Frozen base prefix parent differs')
        starts.append(_day(parent['start']).isoformat())
    if not starts:
        raise ValueError('Frozen catalog retention prefix unavailable')
    priors=[market_sessions(_day(s['target_session'])-timedelta(days=14),
                           _day(s['target_session'])-timedelta(days=1))[-1].isoformat() for s in scopes]
    return min(starts),max(priors)


def catalog_relative(value):
    """Normalize separators only; retain closed-catalog and traversal checks."""
    if type(value) is not str:
        raise ValueError('Catalog parent must be a relative string')
    return value.replace('\\', '/')


def source_read_plan(root, scopes, inventory, canonical_policy):
    """Derive years before metadata reads; private factory proves grants/storage."""
    from .canonical_source_reader import SourceReadPlan
    start,end=source_interval(root,scopes,inventory)
    return SourceReadPlan(tuple(range(_day(start).year,_day(end).year+1)),canonical_policy)


def missing(scopes, inventory):
    """Missing means no usable parent, never discard a deferred retained name."""
    if type(scopes) is not list or not scopes or len(scopes) > 100000:
        raise ValueError('Certified dated source scope required')
    retained = set()
    dates = []
    for item in scopes:
        if type(item) is not dict or set(item) != {'target_session','tickers','original_market_token',
                'scoped_market_token','original_price_token','scoped_price_token','exclusion_policy_hash'}:
            raise ValueError('Exact certified dated scope shape required')
        dates.append(item['target_session']);_day(item['target_session'])
        for key in ('original_market_token','scoped_market_token','original_price_token',
                    'scoped_price_token','exclusion_policy_hash'):
            _hash(item[key])
        names = item['tickers']
        if (type(names) is not list or not names or len(names) != len(set(names))
                or any(type(t) is not str or not re.fullmatch(r'[A-Z0-9.\- ]{1,30}',t) for t in names)):
            raise ValueError('Exact retained ticker identities required')
        retained.update(names)
    if dates != sorted(set(dates)) or len(retained) > 100000:
        raise ValueError('Dated scope inventory is unordered or unbounded')
    rows = inventory['rows']
    if len(rows) != len({r['ticker'] for r in rows}):
        raise ValueError('Base parent inventory duplicates identity')
    usable = {r['ticker'] for r in rows if r['state'] == 'queued'}
    return sorted(retained-usable)


def _wire_positive(value):
    # Existing JSONEachRow transport can represent UInt64 as canonical text.
    # Preserve exact source wire values; bool/float/leading-zero aliases reject.
    if type(value) is int:
        result = value
    elif type(value) is str and re.fullmatch('[1-9][0-9]{0,19}',value):
        result = int(value)
    else:
        raise ValueError('Canonical positive source integer required')
    if not 0 < result <= 2**64-1:
        raise ValueError('Canonical source integer outside UInt64')
    return result


def _validate_rules(rules):
    keys={'token_id','modifier_int','update_high_low','update_last','update_volume'}
    if (type(rules) is not list or not rules or any(type(r) is not dict or set(r)!=keys
            or any(type(v) is not int for v in r.values())
            or not 0 <= r['token_id'] <= 2**32-1
            or any(r[k] not in (0,1) for k in ('update_high_low','update_last','update_volume')) for r in rules)):
        raise ValueError('Exact canonical condition rules required')
    if [r['token_id'] for r in rules] != sorted({r['token_id'] for r in rules}):
        raise ValueError('Canonical condition identities duplicate or unordered')


def _validate_days(ticker, days, start, end, coverage):
    if type(days) is not list or not days or len(days) > 100000:
        raise ValueError('Complete bounded canonical chronology required')
    names = []
    for row in days:
        if type(row) is not dict or row.get('ticker') != ticker:
            raise ValueError('Foreign canonical chronology')
        day = row['source_date'];_day(day);names.append(day)
        if not start <= day <= end:
            raise ValueError('Canonical chronology outside declared interval')
        for field in ('event_count','next_ordinal'):
            _wire_positive(row[field])
        if type(row['last_ordinal']) not in (str,int) or type(row['last_ordinal']) is bool:
            raise ValueError('Canonical ordinal alias rejected')
        ordinal_bounds(row)
    if names != sorted(set(names)) or len(days) != _wire_positive(coverage['days']):
        raise ValueError('Canonical chronology duplicate or incomplete')
    if (coverage['ticker'] != ticker or coverage['first'] != names[0] or coverage['last'] != names[-1]
            or _wire_positive(coverage['events']) != sum(_wire_positive(r['event_count']) for r in days)):
        raise ValueError('Canonical coverage/chronology graph differs')
    _hash(coverage['signature'])


def _continuity_sql(ticker, start, end):
    return ('SELECT * FROM market_sip_compact.events_ordinal_continuity FINAL WHERE '
            f'ticker={source.literal(ticker)} AND source_date BETWEEN {source.literal(start)} '
            f'AND {source.literal(end)} ORDER BY source_date')


def prepare_parent(root, scope, scopes, reader, *, start=None, end=None, inventory=None):
    """Fresh metadata only. Production caller reloads scope certificates first."""
    from .canonical_scoped_campaign import _query
    base = _inventory(root) if inventory is None else inventory
    interval=source_interval(root,scopes,base)
    if (start is not None and start != interval[0]) or (end is not None and end != interval[1]):
        raise ValueError('Canonical interval must preserve frozen prefix and declared DEV cutoff')
    start,end=interval
    if start > end or _day(end) > date.today():
        raise ValueError('Invalid or future canonical source interval')
    gap = missing(scopes,base)
    if not gap:
        raise ValueError('No missing retained metadata parents')
    if ([s['target_session'] for s in scopes] != scope['sessions'] or scope['role'] != 'development'
            or any(s['exclusion_policy_hash'] != scope['exclusion_hash'] for s in scopes)):
        raise ValueError('Development declaration/scope mismatch')
    current = kernel()
    if current['source_files'] != c.hashes():
        raise ValueError('Current kernel source differs')
    rules = _query(reader,c.RULE_SQL)
    _validate_rules(rules)
    reporting = _query(reader,source.reporting_coverage_sql(start,end))
    rows = []
    for ticker in gap:
        targets = [s['target_session'] for s in scopes if ticker in s['tickers']]
        required = [market_sessions(_day(t)-timedelta(days=14),_day(t)-timedelta(days=1))[-1].isoformat()
                    for t in targets]
        if min(required) < start or max(required) > end:
            raise ValueError('Source interval omits exact requested prior')
        coverage = _query(reader,source.coverage_sql(start,end,[ticker]))
        if len(coverage) != 1:
            raise ValueError('Canonical source coverage missing or duplicate')
        days = _query(reader,_continuity_sql(ticker,start,end))
        _validate_days(ticker,days,start,end,coverage[0])
        source.require_reporting_coverage([d['source_date'] for d in days],reporting)
        if not set(required).issubset({d['source_date'] for d in days}):
            raise ValueError('Exact requested prior source missing; no invented zero checkpoint')
        rows.append(dict(ticker=ticker,directory=c.paths(Path('.'),ticker).name,status='queued',reason='',
            coverage=coverage[0],source_days=days,continuity_hash=c.digest(days),reporting_rows=reporting,
            reporting_hash=c.digest(reporting),requested_targets=targets))
    if _query(reader,c.RULE_SQL) != rules:
        raise ValueError('Canonical rules changed during metadata preparation')
    # This is a bounded snapshot consistency check, not a transactional source
    # lock. Workers and the final archive reader independently recheck again.
    if not _same(_query(reader,source.reporting_coverage_sql(start,end)),reporting):
        raise ValueError('Canonical reporting changed during metadata preparation')
    for row in rows:
        ticker=row['ticker']
        if (not _same(_query(reader,source.coverage_sql(start,end,[ticker])),[row['coverage']])
                or not _same(_query(reader,_continuity_sql(ticker,start,end)),row['source_days'])):
            raise ValueError('Canonical coverage or continuity changed during metadata preparation')
    value = dict(version=VERSION,start=start,end=end,source_policy=HISTORICAL_POLICY,input_policy=POLICY,
        reporting_revision=source.REPORTING_REVISION,reporting_coverage_contract='source-plan-v1',
        extraction_version=EXTRACTION_VERSION,band_config=deepcopy(CONFIG),rules=rules,rows=rows,
        declaration=deepcopy(scope),scopes=deepcopy(scopes),base_inventory=deepcopy(base),
        base_inventory_hash=c.digest(base),source_files=current['source_files'],software=current['software'],
        metadata_parent_sources=sources(),
        population_contract='exact certified retained market-ticker gaps; no broker/fundamental membership claim')
    value['plan_hash'] = c.digest(value)
    verify_parent(root,value,inventory=base)
    return value


def verify_parent(root, parent, *, inventory=None, scope=None, scopes=None):
    if (type(parent) is not dict or set(parent) != KEYS or parent['version'] != VERSION
            or parent['plan_hash'] != c.digest({k:v for k,v in parent.items() if k!='plan_hash'})):
        raise ValueError('Exact canonical metadata parent contract required')
    _hash(parent['plan_hash'])
    current = kernel()
    expected = dict(source_policy=HISTORICAL_POLICY,input_policy=POLICY,
        reporting_revision=source.REPORTING_REVISION,reporting_coverage_contract='source-plan-v1',
        extraction_version=EXTRACTION_VERSION,band_config=CONFIG,source_files=current['source_files'],
        software=current['software'],metadata_parent_sources=sources(),
        population_contract='exact certified retained market-ticker gaps; no broker/fundamental membership claim')
    if current['source_files'] != c.hashes() or any(not _same(parent[k],v) for k,v in expected.items()):
        raise ValueError('Canonical metadata producer/policy differs')
    base = _inventory(root) if inventory is None else inventory
    if not _same(parent['base_inventory'],base) or parent['base_inventory_hash'] != c.digest(base):
        raise ValueError('Canonical metadata base inventory differs')
    if scope is not None and not _same(parent['declaration'],scope):
        raise ValueError('Canonical metadata declaration differs')
    if scopes is not None and not _same(parent['scopes'],scopes):
        raise ValueError('Canonical metadata source scopes differ')
    declaration = parent['declaration']
    from .canonical_scoped_campaign import SCOPE_KEYS, VERSION as SCOPE_VERSION
    if (type(declaration) is not dict or set(declaration) != SCOPE_KEYS
            or declaration['schema'] != SCOPE_VERSION or declaration['role'] != 'development'
            or type(declaration['configuration_number']) is not int or declaration['configuration_number'] <= 0
            or type(declaration['configuration_revision_id']) is not str or not declaration['configuration_revision_id']
            or type(declaration['sessions']) is not list
            or declaration['sessions'] != [s['target_session'] for s in parent['scopes']]
            or any(s['exclusion_policy_hash'] != declaration['exclusion_hash'] for s in parent['scopes'])):
        raise ValueError('Canonical metadata declared authority differs')
    _hash(declaration['configuration_payload_hash']);_hash(declaration['exclusion_hash'])
    if any(market_sessions(_day(day),_day(day)) != [_day(day)] or _day(day) > date.today()
           for day in declaration['sessions']):
        raise ValueError('Canonical metadata target is not a past exchange session')
    gap = missing(parent['scopes'],base)
    if type(parent['rows']) is not list or [r['ticker'] for r in parent['rows']] != gap or not gap:
        raise ValueError('Canonical metadata is not exact disjoint retained gap set')
    start,end = parent['start'],parent['end'];_day(start);_day(end)
    if (start,end) != source_interval(root,parent['scopes'],base) or start > end or _day(end) > date.today():
        raise ValueError('Canonical metadata interval differs')
    _validate_rules(parent['rules'])
    for row in parent['rows']:
        if (type(row) is not dict or set(row) != ROW_KEYS or row['status'] != 'queued' or row['reason'] != ''
                or row['directory'] != c.paths(Path('.'),row['ticker']).name
                or row['continuity_hash'] != c.digest(row['source_days'])
                or row['reporting_hash'] != c.digest(row['reporting_rows'])):
            raise ValueError('Canonical metadata member content differs')
        _validate_days(row['ticker'],row['source_days'],start,end,row['coverage'])
        source.require_reporting_coverage([d['source_date'] for d in row['source_days']],row['reporting_rows'])
        targets=[s['target_session'] for s in parent['scopes'] if row['ticker'] in s['tickers']]
        if not _same(row['requested_targets'],targets):
            raise ValueError('Canonical metadata dated membership differs')
        for target in targets:
            prior=market_sessions(_day(target)-timedelta(days=14),_day(target)-timedelta(days=1))[-1].isoformat()
            if prior not in {d['source_date'] for d in row['source_days']}:
                raise ValueError('Canonical metadata exact prior missing')
    return parent


def successor(root, parent, ticker, *, inventory=None):
    verify_parent(root,parent,inventory=inventory)
    _, value = legacy_successor(root,parent,ticker)
    value.update(version=c.VERSION,derivation=DERIVATION,metadata_parent_contract=VERSION,
                 metadata_parent_hash=parent['plan_hash'])
    value['plan_hash']=c.digest({k:v for k,v in value.items() if k!='plan_hash'})
    return Path(root)/SUCCESSOR_DIRECTORY/value['plan_hash'],value


def load_parent(root, path, expected_hash, *, scope=None, scopes=None, inventory=None):
    _hash(expected_hash)
    expected = (Path(root)/relative(expected_hash)/'plan.json').resolve()
    if Path(path).resolve() != expected:
        raise ValueError('Canonical metadata parent path is not content-addressed')
    value = c.read(expected)
    verify_parent(root,value,inventory=inventory,scope=scope,scopes=scopes)
    if value['plan_hash'] != expected_hash:
        raise ValueError('Canonical metadata parent hash differs')
    return value


def publish_parent(root, parent):
    verify_parent(root,parent)
    target = Path(root)/relative(parent['plan_hash'])/'plan.json'
    with c.exclusive(Path(root)/PARENT_DIRECTORY/'publication.lock'):
        c.write(target,parent)
    return target


def overlay_inventory(root, parent, *, inventory=None, scope=None, scopes=None):
    base = _inventory(root) if inventory is None else inventory
    verify_parent(root,parent,inventory=base,scope=scope,scopes=scopes)
    value=deepcopy(base);gap={r['ticker'] for r in parent['rows']}
    value['rows']=[r for r in value['rows'] if r['ticker'] not in gap]
    for row in parent['rows']:
        output,child=successor(root,parent,row['ticker'],inventory=base)
        value['rows'].append(dict(ticker=row['ticker'],state='queued',parent=relative(parent['plan_hash']),
            parent_hash=parent['plan_hash'],plan_hash=child['plan_hash'],output=str(output.relative_to(root)),
            directory=row['directory'],before=(_day(parent['end'])+timedelta(days=1)).isoformat(),
            sessions=_wire_positive(row['coverage']['days'])))
    value['rows'].sort(key=lambda r:(-r.get('sessions',0),r['ticker']))
    value['metadata_parent_hash']=parent['plan_hash']
    value['base_inventory_hash']=c.digest(base)
    value['manifest_hash']=c.digest({k:v for k,v in value.items() if k!='manifest_hash'})
    return value


def verify_manifest_reference(proof, *, root=None):
    ref=proof.get('metadata_parent_reference')
    if (type(ref) is not dict or set(ref)!={'relative','plan_hash','base_inventory_hash','retained_inventory_hash'}
            or not is_relative(ref['relative']) or ref['relative']!=relative(ref['plan_hash'])):
        raise ValueError('Exact metadata parent manifest reference required')
    for field in ('plan_hash','base_inventory_hash','retained_inventory_hash'):
        _hash(ref[field])
    retained=proof.get('retained_parent_inventory')
    if type(retained) is not list or ref['retained_inventory_hash']!=c.digest(retained):
        raise ValueError('Metadata parent retained inventory digest differs')
    names=[r['ticker'] for r in retained]
    if names!=sorted(set(names)):
        raise ValueError('Metadata parent retained inventory identities differ')
    if root is None:
        return None  # structural only, never installed or source authority
    parent=load_parent(root,Path(root)/ref['relative']/'plan.json',ref['plan_hash'],
                       scope=proof['declaration'],scopes=proof['scopes'])
    if ref['base_inventory_hash']!=parent['base_inventory_hash']:
        raise ValueError('Metadata manifest base inventory differs')
    combined=overlay_inventory(root,parent)
    by_ticker={r['ticker']:r for r in combined['rows']}
    expected=[by_ticker[t] for t in sorted({t for s in parent['scopes'] for t in s['tickers']})]
    if not _same(retained,expected):
        raise ValueError('Metadata manifest complete overlay inventory differs')
    return parent
