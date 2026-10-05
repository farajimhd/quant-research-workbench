"""Explicit SELECT-only canonical prior-day V7 route; never a legacy fallback."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import json

from src.backend.structural_v7_seed import _literal, _rows
from src.backend.backtest_declared_ladder_seed import _previous_session
from src.market_engine.canonical_v7_checkpoint import (
    CanonicalV7Selector, TABLE, VERSION, MAX_CHECKPOINT_BYTES,
    checkpoint_record, hash_value,
)
from src.market_engine.historical_level_checkpoint import digest
from src.trading_runtime.journal_contract import canonical_json


def selector_from_configuration(configuration):
    release = configuration.payload['strategy']['numbered_release']
    value = release.get('canonical_v7_source')
    if value is None:
        raise ValueError('Release has no explicit canonical V7 source selector')
    if type(value) is not dict or set(value) != {'namespace', 'source_plan_hash', 'plan'} or value['namespace'] != VERSION:
        raise ValueError('Unknown canonical V7 source namespace')
    selector = CanonicalV7Selector(canonical_json(value['plan']), value['source_plan_hash'])
    selector.plan()
    return selector


def _readonly(client, selector):
    if type(selector) is not CanonicalV7Selector:
        raise ValueError('Canonical V7 requires explicit sealed selector')
    selector.plan()
    if client.execute("SELECT getSetting('readonly')").strip() != '1':
        raise ValueError('Canonical V7 requires a dedicated SELECT-only reader')


def _record_rows(client, selector, *, session, tickers):
    names = ','.join(_literal(name) for name in tickers)
    return _rows(client, f'SELECT record_json,record_hash FROM {TABLE} '
        f'WHERE source_plan_hash={_literal(selector.source_plan_hash)} '
        f'AND session_date=toDate({_literal(session)}) AND ticker IN ({names}) '
        f'LIMIT {len(tickers)+1} FORMAT JSONEachRow')


def _decode(row):
    raw = row['record_json']
    if type(raw) is not str or len(raw.encode('utf-8')) > MAX_CHECKPOINT_BYTES:
        raise ValueError('Canonical V7 checkpoint exceeds its bounded payload')
    record = json.loads(raw)
    if (type(record) is not dict or set(record) != {'version','receipt','checkpoint','level_count','observation_count','record_hash'}
            or record['version'] != VERSION or canonical_json(record) != raw
            or record['record_hash'] != hash_value(row['record_hash'])
            or digest({key:value for key,value in record.items() if key != 'record_hash'}) != record['record_hash']):
        raise ValueError('Canonical V7 stored record integrity differs')
    return record


def _verify_decoded_record(selector, record, parent=None):
    receipt = record['receipt']
    predecessor = None
    if receipt['parent_checkpoint_hash'] is not None:
        if parent is None or parent['checkpoint']['checkpoint_hash'] != receipt['parent_checkpoint_hash']:
            raise ValueError('Canonical V7 predecessor is missing or duplicated')
        from src.market_engine.canonical_v7_checkpoint import validate_checkpoint_identity
        validate_checkpoint_identity(selector, parent['receipt'], parent['checkpoint'])
        predecessor = parent['checkpoint']
        if (parent['receipt']['ticker'] != receipt['ticker']
                or parent['receipt']['session_date'] != _previous_session(date.fromisoformat(receipt['session_date'])).isoformat()
                or predecessor['checkpoint_hash'] != digest({key:value for key,value in predecessor.items() if key != 'checkpoint_hash'})):
            raise ValueError('Canonical V7 predecessor differs from exact prior session')
        if receipt['prefix_price_seconds'] != parent['receipt']['prefix_price_seconds'] + receipt['price_seconds']:
            raise ValueError('Canonical V7 cumulative price evidence differs from predecessor')
    if checkpoint_record(selector, receipt, record['checkpoint'], predecessor=predecessor) != record:
        raise ValueError('Canonical V7 stored record differs from certified checkpoint')
    return record


def _verified_records(client, selector, rows):
    if sum(len(row['record_json'].encode('utf-8')) for row in rows) > MAX_CHECKPOINT_BYTES:
        raise ValueError('Canonical V7 checkpoint batch exceeds its 64 MiB source budget')
    records = [_decode(row) for row in rows]
    scopes = {}
    for record in records:
        receipt = record['receipt']
        if receipt['parent_checkpoint_hash'] is not None:
            day = _previous_session(date.fromisoformat(receipt['session_date'])).isoformat()
            scopes.setdefault(day,set()).add(receipt['ticker'])
    parents = {}
    for day,names in scopes.items():
        selected = tuple(sorted(names))
        prior_rows = _record_rows(client,selector,session=day,tickers=selected)
        if len(prior_rows) != len(selected) or sum(len(row['record_json'].encode('utf-8')) for row in prior_rows) > MAX_CHECKPOINT_BYTES:
            raise ValueError('Canonical V7 predecessor scope is missing, duplicate or over budget')
        for row in prior_rows:
            parent = _decode(row)
            key = (parent['receipt']['session_date'],parent['receipt']['ticker'])
            if key in parents or key[0]!=day or key[1] not in names:
                raise ValueError('Canonical V7 predecessor changed its exact date/ticker scope')
            parents[key] = parent
    return [_verify_decoded_record(selector,record,parents.get((
        _previous_session(date.fromisoformat(record['receipt']['session_date'])).isoformat(),record['receipt']['ticker'])))
        for record in records]


class CanonicalV7ExecutionUnavailable(RuntimeError):
    """The real producer/Keeper source seal bridge has not been implemented."""


@dataclass(frozen=True, slots=True)
class CanonicalSeedInspection:
    build_id: str
    source_plan_hash: str
    units: tuple
    inspection_token: str
    executable: bool = False


def _unit(record, target):
    from datetime import datetime, timezone
    checkpoint, receipt = record['checkpoint'], record['receipt']
    return {'ticker':receipt['ticker'], 'session_date':receipt['session_date'],
        'available_at':datetime.fromtimestamp(checkpoint['available_at'], timezone.utc).isoformat(),
        'source_checkpoint_hash':checkpoint['checkpoint_hash'], 'source_plan_hash':receipt['source_plan_hash'],
        'level_count':record['level_count'], 'observation_count':record['observation_count'],
        'input_policy':checkpoint['input_policy'], 'backtest_session':target,
        'canonical_namespace':VERSION, 'canonical_record_hash':record['record_hash']}


def certified_canonical_seed_plan(market, client, selector):
    raise CanonicalV7ExecutionUnavailable('Canonical V7 execution requires independently verified producer/Keeper source coverage seals; prototype inspection is not authority')


def inspect_canonical_seed_plan(market, client, selector):
    """Integrity-only diagnostic; cannot satisfy executable CertifiedSeedPlan type."""
    _readonly(client, selector)
    plan = selector.plan()
    scope = sorted({(unit.session_date,unit.ticker) for unit in market.units if unit.stage == 'bars'})
    if not scope or len(scope) > 8192 or any(ticker not in plan['tickers'] for _,ticker in scope):
        raise ValueError('Canonical V7 frozen bars scope exceeds sealed source population')
    units = []
    for day in market.sessions:
        prior = _previous_session(date.fromisoformat(day)).isoformat()
        names = tuple(ticker for target,ticker in scope if target == day)
        for offset in range(0,len(names),8):
            batch = names[offset:offset+8]
            rows = _record_rows(client, selector, session=prior, tickers=batch)
            if len(rows) != len(batch):
                raise ValueError('Canonical V7 prior-day checkpoint is missing or duplicated')
            records = _verified_records(client,selector,rows)
            if {r['receipt']['ticker'] for r in records} != set(batch) or any(r['receipt']['session_date'] != prior for r in records):
                raise ValueError('Canonical V7 prior-day scope differs from frozen request')
            units.extend(_unit(record,day) for record in records)
    units = tuple(sorted(units,key=lambda unit:(unit['backtest_session'],unit['ticker'])))
    return CanonicalSeedInspection(market.build_id,selector.source_plan_hash,units,digest(units))


def load_canonical_seed(client, *, ticker, session, coverage, selector):
    raise CanonicalV7ExecutionUnavailable('Canonical V7 execution requires independently verified producer/Keeper source coverage seals; prototype inspection is not authority')


def inspect_canonical_seed(client, *, ticker, session, coverage, selector):
    _readonly(client, selector)
    prior = _previous_session(session).isoformat()
    rows = _record_rows(client, selector, session=prior, tickers=(ticker,))
    if len(rows) != 1:
        raise ValueError('Canonical V7 exact prior checkpoint is missing or duplicated')
    record, = _verified_records(client,selector,rows)
    if _unit(record,session.isoformat()) != coverage:
        raise ValueError('Canonical V7 source changed after preflight')
    seed = deepcopy(record['checkpoint'])
    seed['source_checkpoint_hash'] = seed['checkpoint_hash']
    seed['checkpoint_hash'] = digest({key:value for key,value in seed.items() if key != 'checkpoint_hash'})
    return seed
