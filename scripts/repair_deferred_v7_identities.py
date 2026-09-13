"""Audit/repair only a frozen V7 campaign's deferred common shares.

scope writes a classification report; prepare captures fresh broker/provider
evidence and immutable before/after rows; apply performs checked append-only
retirements and rebuilds current publications; supplement creates a separate
common-share-only campaign plan. No command edits the original campaign.
"""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
from collections import Counter,defaultdict
from dataclasses import asdict
from datetime import datetime,timezone
import json
from types import SimpleNamespace
import uuid
import socket

from research.level_book.v7 import campaign as c
from services.reference_gateway.deferred_identity_repair import classify_deferred,decide_retirements
from services.reference_gateway.ibkr_contract_identity import normalize_equity_symbol,expected_ibkr_listing_exchange
from services.reference_gateway.providers import IbkrReferenceClient,MassiveReferenceClient
from services.reference_gateway.canonical_graph_writer import insert_json_each_row
from services.reference_gateway.publication_rebuild import rebuild_tradable_publications

BASE=c.WORKSTATION_RUNTIME_ROOT/'level-book-v7'
TABLE_KEYS={'id_symbol_v1':'symbol_id','id_source_mapping_v1':'source_mapping_id'}
WRITE_TABLES=tuple(TABLE_KEYS)+('feature_tradable_universe_v1','feature_scanner_static_v1')


def in_list(values):
    return '('+','.join(c.literal(v) for v in sorted(set(values)))+')' if values else "('')"


def save(path,value):
    value={**value,'hash':c.digest(value)}
    c.write(path,value)
    return value


def verified(path):
    value=c.read(path)
    if value.get('hash')!=c.digest({k:v for k,v in value.items() if k!='hash'}):
        raise ValueError(f'Artifact hash mismatch: {path}')
    return value


def scope(parent,output):
    p=c.checked_plan(parent)
    if (output/'scope.json').exists():
        result=verified(output/'scope.json')
        if result['parent_plan_hash']!=p['plan_hash']:
            raise ValueError('Existing scope belongs to another campaign')
        print('Using frozen deferred scope:',output/'scope.json',flush=True)
        return result
    inventory=c.query('SELECT current_ticker,active,source_payload_json FROM q_live.market_ticker_event_entity_v1 FINAL WHERE is_deleted=0')
    provider=[]
    for r in inventory:
        item=json.loads(r['source_payload_json']);item.setdefault('ticker',r['current_ticker']);item['active']=bool(r['active']);provider.append(item)
    rows=classify_deferred(p['rows'],provider)
    result=save(output/'scope.json',dict(version='deferred-common-shares-1',parent_plan_hash=p['plan_hash'],
        created_at=c.now(),policy='Only originally deferred common shares (CS); completed population unchanged',rows=rows))
    print('Deferred scope:',dict(Counter(r['classification'] for r in rows)),flush=True)
    return result


def auth():
    ibkr=IbkrReferenceClient(base_url=os.environ.get('IBKR_CPAPI_BASE_URL','https://localhost:5000/v1/api'),timeout_seconds=15)
    state=ibkr.auth_status()
    if not state.get('authenticated') or not state.get('connected'):
        raise ValueError('IBKR must be authenticated and connected; no stale identity fallback')
    return ibkr


def prepare(parent,output):
    scoped=verified(output/'scope.json')
    p=c.checked_plan(parent)
    if scoped['parent_plan_hash']!=p['plan_hash']:
        raise ValueError('Scope belongs to another campaign')
    if (output/'repair-plan.json').exists():
        existing=verified(output/'repair-plan.json')
        if existing['parent_plan_hash']!=p['plan_hash'] or existing['scope_hash']!=scoped['hash']:
            raise ValueError('Existing repair provenance mismatch')
        print('Using immutable repair evidence; use a new --output for fresh evidence:',output/'repair-plan.json',flush=True)
        return existing
    ibkr=auth()
    massive=MassiveReferenceClient(base_url=os.environ.get('MASSIVE_BASE_URL','https://api.massive.com'),
        api_key=os.environ.get('MASSIVE_API_KEY',''),page_limit=1000,max_pages=100)
    print('Fetching fresh provider inventory…',flush=True)
    result=massive.fetch_active_us_stock_tickers()
    if result.saturated:
        raise ValueError('Provider inventory saturated; refusing incomplete evidence')
    provider=defaultdict(list)
    for item in result.tickers:
        provider[normalize_equity_symbol(item['ticker'])].append(item)
    selected=[r['ticker'] for r in scoped['rows'] if r['classification']=='common_share_candidate']
    published=c.query('SELECT * FROM q_live.feature_tradable_universe_v1 FINAL WHERE universe_date=(SELECT max(universe_date) FROM q_live.feature_tradable_universe_v1) AND is_tradable=1 AND ticker IN '+in_list(selected))
    symbols=c.query('SELECT * FROM q_live.id_symbol_v1 FINAL WHERE symbol_id IN '+in_list(r['symbol_id'] for r in published))
    listings=c.query('SELECT * FROM q_live.id_listing_v1 FINAL WHERE listing_id IN '+in_list(r['listing_id'] for r in symbols))
    relevant=[values[0] for ticker,values in provider.items() if ticker in {normalize_equity_symbol(t) for t in selected} and len(values)==1]
    figis={x for r in relevant for x in (r.get('composite_figi'),r.get('share_class_figi')) if x}
    identifiers=c.query('SELECT * FROM q_live.id_security_identifier_v1 FINAL WHERE identifier_value_normalized IN '+in_list(figis))
    by_exchange=defaultdict(set)
    for exchange in sorted({expected_ibkr_listing_exchange(r.get('primary_exchange')) for r in relevant}-{''}):
        print('Broker primary-contract inventory:',exchange,flush=True)
        for r in ibkr.fetch_all_stock_conids(exchange):
            if str(r.get('conid','')).isdigit():
                by_exchange[(exchange,normalize_equity_symbol(r.get('ticker')))].add(int(r['conid']))
    conids={int(l['ibkr_conid']) for l in listings if str(l.get('ibkr_conid','')).isdigit()}
    candidates={}
    for ticker in selected:
        values=provider.get(normalize_equity_symbol(ticker),[])
        item=values[0] if len(values)==1 else {}
        ids=set(by_exchange[(expected_ibkr_listing_exchange(item.get('primary_exchange')),normalize_equity_symbol(ticker))])
        ids.update(int(r['ibkr_conid']) for r in published if r['ticker']==ticker and str(r.get('ibkr_conid','')).isdigit())
        candidates[ticker]=ids;conids.update(ids)
    definitions={}
    ordered=sorted(conids)
    for offset in range(0,len(ordered),200):
        for row in ibkr.fetch_security_definitions(ordered[offset:offset+200]):
            definitions[int(row.get('conid') or row.get('con_id'))]=row
    timestamp=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    run_id='deferred_identity_'+uuid.uuid4().hex
    decisions=[];changes=[]
    for ticker in selected:
        values=provider.get(normalize_equity_symbol(ticker),[])
        if len(values)!=1:
            decisions.append(dict(ticker=ticker,status='blocked',reason='current_provider_missing_or_ambiguous'));continue
        scoped_symbols=[s for s in symbols if normalize_equity_symbol(s['ticker'])==normalize_equity_symbol(ticker)]
        decision=decide_retirements(ticker,values[0],scoped_symbols,listings,identifiers,
            [definitions[i] for i in sorted(candidates[ticker]) if i in definitions],timestamp[:10])
        decision['provider']=values[0]
        decisions.append(decision)
        if decision['status']=='repairable':
            for before in decision['losers']:
                after=dict(before,status='inactive',primary_symbol_flag=0,last_seen_at_utc=timestamp,
                    source_run_id=run_id,source_content_sha256=c.digest(decision),inserted_at=timestamp)
                changes.append(dict(table='id_symbol_v1',key=before['symbol_id'],before=before,after=after))
    retired={x['key'] for x in changes}
    # Broker calls can exceed the server's keep-alive period. Reopen SQL after
    # that phase instead of reusing an idle socket or retrying a possible write.
    for client in c.CLIENTS.values():
        client.close()
    mappings=c.query('SELECT * FROM q_live.id_source_mapping_v1 FINAL WHERE mapped_entity_id IN '+in_list(retired)+" AND mapping_status='active' AND source_system IN ('massive','market_reference')")
    for before in mappings:
        after=dict(before,mapping_status='inactive',resolved_at_utc=timestamp,source_run_id=run_id,
                   source_content_sha256=c.digest(dict(retired_symbol=before['mapped_entity_id'],run_id=run_id)),inserted_at=timestamp)
        changes.append(dict(table='id_source_mapping_v1',key=before['source_mapping_id'],before=before,after=after))
    value=save(output/'repair-plan.json',dict(version='deferred-identity-repair-1',created_at=c.now(),run_id=run_id,
        parent_plan_hash=p['plan_hash'],scope_hash=scoped['hash'],decisions=decisions,changes=changes,
        canonical_listings=listings,canonical_identifiers=identifiers,broker_definitions=list(definitions.values())))
    print('Decisions:',dict(Counter(r['status'] for r in decisions)),flush=True)
    print('Blocked reasons:',dict(Counter(r['reason'] for r in decisions if r['status']=='blocked')),flush=True)
    print('Proposed append-only rows:',len(changes),flush=True)
    return value


def check_storage():
    rows=c.query('SELECT name,storage_policy FROM system.tables WHERE database=\'q_live\' AND name IN '+in_list(WRITE_TABLES))
    if {r['name'] for r in rows}!=set(WRITE_TABLES) or any(r['storage_policy']!='live_market_ssd' for r in rows):
        raise ValueError('Identity/publication writers require live_market_ssd policies')
    parts=c.query('SELECT table,disk_name,count() n FROM system.parts WHERE database=\'q_live\' AND active AND table IN '+in_list(WRITE_TABLES)+' GROUP BY table,disk_name')
    if any(r['disk_name']!='live_market_ssd' for r in parts):
        raise ValueError('Existing identity/publication parts are not on live_market_ssd')


def apply(parent,output):
    plan=verified(output/'repair-plan.json');scoped=verified(output/'scope.json')
    if plan['parent_plan_hash']!=c.checked_plan(parent)['plan_hash'] or plan['scope_hash']!=scoped['hash']:
        raise ValueError('Repair provenance mismatch')
    if (output/'applied.json').exists():
        receipt=verified(output/'applied.json')
        if receipt['repair_hash']!=plan['hash']:
            raise ValueError('Applied receipt belongs to another repair')
        print('Repair already completed at',receipt['at'],'; no writes repeated',flush=True)
        return receipt
    if (datetime.now(timezone.utc)-datetime.fromisoformat(plan['created_at'])).total_seconds()>14400:
        raise ValueError('Broker evidence is older than four hours; prepare a new repair')
    auth();check_storage()
    # The managed gateway must be stopped for this bounded maintenance window.
    # Optimistic row checks below additionally reject intervening canonical edits.
    with socket.socket() as probe:
        probe.settimeout(2)
        if probe.connect_ex(('127.0.0.1',8799))==0:
            raise ValueError('Stop the managed Reference Gateway before applying identity repairs')
    allowed={r['ticker'] for r in scoped['rows'] if r['classification']=='common_share_candidate'}
    if any(r['ticker'] not in allowed for r in plan['decisions']):
        raise ValueError('Repair expanded beyond deferred common shares')
    for table,key,expected in (
        ('id_listing_v1','listing_id',plan['canonical_listings']),
        ('id_security_identifier_v1','security_identifier_id',plan['canonical_identifiers']),
        ('id_symbol_v1','symbol_id',[d['winner'] for d in plan['decisions'] if d['status'] in ('repairable','already_unique')]),
    ):
        current={r[key]:r for r in c.query(f'SELECT * FROM q_live.{table} FINAL WHERE {key} IN '+in_list(r[key] for r in expected))}
        if any(current.get(r[key])!=r for r in expected):
            raise ValueError('Identity evidence changed since preparation: '+table)
    # Check every table before the first write. A resumed invocation also accepts
    # exactly the recorded after-state, never a different concurrent edit.
    pending_by_table={}
    for table,key in TABLE_KEYS.items():
        changes=[x for x in plan['changes'] if x['table']==table]
        current={r[key]:r for r in c.query(f'SELECT * FROM q_live.{table} FINAL WHERE {key} IN '+in_list(x['key'] for x in changes))}
        pending_by_table[table]=checked_pending(changes,current)
    population=c.query('SELECT symbol_id,listing_id,security_id,ticker,ibkr_conid,is_tradable,exclusion_reason FROM q_live.feature_tradable_universe_v1 FINAL WHERE universe_date=(SELECT max(universe_date) FROM q_live.feature_tradable_universe_v1) AND ticker NOT IN '+in_list(allowed)+' ORDER BY symbol_id')
    baseline=output/'unrelated-publication-before.json'
    if baseline.exists():
        saved=verified(baseline)
        if saved['repair_hash']!=plan['hash']:
            raise ValueError('Publication baseline belongs to another repair')
        population=saved['rows']
    else:
        save(baseline,dict(rows=population,repair_hash=plan['hash']))
    client=c.ClickHouseHttpClient(c.default_clickhouse_url(),c.default_clickhouse_user(),c.default_clickhouse_password())
    for table,key in TABLE_KEYS.items():
        changes=[x for x in plan['changes'] if x['table']==table]
        pending=pending_by_table[table]
        if pending:
            insert_json_each_row(client,'q_live',table,pending)
        actual={r[key]:r for r in c.query(f'SELECT * FROM q_live.{table} FINAL WHERE {key} IN '+in_list(x['key'] for x in changes))}
        if any(actual.get(x['key'])!=x['after'] for x in changes):
            raise ValueError('Identity write verification failed: '+table)
        print(table,': verified',len(changes),'rows',flush=True)
    cfg=SimpleNamespace(execute=True,test_write_mode=False,rebuild_tradable_in_test_mode=False,clickhouse_write_database='q_live')
    rebuilt=rebuild_tradable_publications(cfg,reason='verified deferred V7 common-share identity repair')
    for connection in c.CLIENTS.values():
        connection.close()
    check_storage()
    unchanged=c.query('SELECT symbol_id,listing_id,security_id,ticker,ibkr_conid,is_tradable,exclusion_reason FROM q_live.feature_tradable_universe_v1 FINAL WHERE universe_date=(SELECT max(universe_date) FROM q_live.feature_tradable_universe_v1) AND ticker NOT IN '+in_list(allowed)+' ORDER BY symbol_id')
    prior={r['symbol_id']:r for r in population}
    after={r['symbol_id']:r for r in unchanged}
    if any(after.get(k)!=v for k,v in prior.items()):
        raise ValueError('An existing unrelated publication record changed')
    # A managed sync can insert canonical symbols after its last publication.
    # A shared rebuild legitimately exposes those; require timestamped evidence
    # that they predate this repair and persist that evidence in the receipt.
    added=set(after)-set(prior)
    additions=c.query('SELECT * FROM q_live.id_symbol_v1 FINAL WHERE symbol_id IN '+in_list(added))
    cutoff=datetime.fromisoformat(plan['created_at'])
    if len(additions)!=len(added) or any(
        r['source_run_id']==plan['run_id'] or
        datetime.fromisoformat(r['inserted_at']).replace(tzinfo=timezone.utc)>=cutoff
        for r in additions):
        raise ValueError('Unrelated addition does not have pre-repair canonical provenance')
    current=c.query('SELECT * FROM q_live.feature_tradable_universe_v1 FINAL WHERE universe_date=(SELECT max(universe_date) FROM q_live.feature_tradable_universe_v1) AND is_tradable=1 AND ticker IN '+in_list(allowed))
    counts=Counter(r['ticker'] for r in current)
    verified_tickers=[]
    for decision in plan['decisions']:
        if decision['status'] not in ('repairable','already_unique'):
            continue
        matches=[r for r in current if r['ticker']==decision['ticker']]
        if counts[decision['ticker']]!=1 or matches[0]['symbol_id']!=decision['winner']['symbol_id'] or str(matches[0]['ibkr_conid'])!=str(decision['broker']['conid']):
            raise ValueError('Publication did not retain exactly the verified contract: '+decision['ticker'])
        verified_tickers.append(decision['ticker'])
    save(output/'applied.json',dict(repair_hash=plan['hash'],at=c.now(),verified_tickers=verified_tickers,
        preexisting_canonical_additions=additions,publication_rebuild=asdict(rebuilt)))
    print('Verified current unique common-share mappings:',len(verified_tickers),flush=True)


def checked_pending(changes,current):
    pending=[]
    for change in changes:
        if current.get(change['key'])==change['after']:
            continue
        if current.get(change['key'])!=change['before']:
            raise ValueError('Canonical identity changed since preparation: '+change['key'])
        pending.append(change['after'])
    return pending


def supplement(parent,output):
    original=c.checked_plan(parent);repair=verified(output/'repair-plan.json');applied=verified(output/'applied.json')
    if applied['repair_hash']!=repair['hash'] or repair['parent_plan_hash']!=original['plan_hash']:
        raise ValueError('Supplement authority mismatch')
    target=output/'common-share-supplement'
    if (target/'plan.json').exists():
        existing=c.checked_plan(target)
        if existing['repair_hash']!=repair['hash'] or existing['parent_plan_hash']!=original['plan_hash']:
            raise ValueError('Existing supplement belongs to another repair')
        print('Verified existing supplement:',target,flush=True)
        return existing
    eligible=set(applied['verified_tickers'])
    current=c.query('SELECT * FROM q_live.feature_tradable_universe_v1 FINAL WHERE universe_date=(SELECT max(universe_date) FROM q_live.feature_tradable_universe_v1) AND is_tradable=1 AND ticker IN '+in_list(eligible))
    rows=[]
    for d in repair['decisions']:
        if d['ticker'] not in eligible:
            continue
        identities=[r for r in current if r['ticker']==d['ticker'] and r['symbol_id']==d['winner']['symbol_id'] and str(r['ibkr_conid'])==str(d['broker']['conid'])]
        if len(identities)!=1 or sum(r['ticker']==d['ticker'] for r in current)!=1 or d['provider']['type']!='CS':
            raise ValueError('Verified common-share mapping changed: '+d['ticker'])
        market_ticker=d['provider_ticker']
        coverage=c.query(c.coverage_sql(original['start'],original['end'],[market_ticker]))
        identity={k:identities[0].get(k) for k in ('symbol_id','listing_id','security_id','ibkr_conid','source_run_id')}
        identity.update(ticker=market_ticker,massive_ticker=market_ticker)
        rows.append(dict(ticker=market_ticker,requested_ticker=d['ticker'],identity=[identity],directory=c.paths(Path('.'),market_ticker).name,
                         coverage=coverage[0] if coverage else None,status='queued' if coverage else 'deferred',reason='' if coverage else 'no certified canonical history for verified market ticker'))
    if len({r['ticker'] for r in rows})!=len(rows):
        raise ValueError('Several requested stocks map to one market ticker')
    rules=c.query(c.RULE_SQL)
    if rules!=original['rules']:
        raise ValueError('Canonical condition policy changed; requires a separately validated build')
    p={k:v for k,v in original.items() if k not in ('plan_hash','rows')}
    p.update(created_at=c.now(),rows=rows,parent_plan_hash=original['plan_hash'],repair_hash=repair['hash'],
             population_contract='Originally deferred CS common shares only; current broker/FIGI-verified mappings; no historical ticker splicing',
             identity_verified_at=applied['at'])
    p['plan_hash']=c.digest(p)
    target=output/'common-share-supplement';c.write(target/'plan.json',p)
    c.checked_plan(target)
    print('Supplement:',target,dict(Counter(r['status'] for r in rows)),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('scope','prepare','apply','supplement'))
    parser.add_argument('--parent',type=Path,default=BASE/'all-tradable-20250101-20260912-mle-v1')
    parser.add_argument('--output',type=Path,default=BASE/'deferred-common-share-repair-20260913')
    args=parser.parse_args()
    if not BASE.exists() or not args.output.resolve().is_relative_to(BASE.resolve()):
        raise ValueError('Required workstation runtime root unavailable or invalid')
    args.output.mkdir(parents=True,exist_ok=True)
    c.load_env_files(c.discover_clickhouse_env_files(),verbose=False)
    with c.exclusive(args.output/'repair.lock'):
        globals()[args.command](args.parent,args.output)


if __name__=='__main__':
    main()
