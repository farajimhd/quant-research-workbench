"""Coverage-last shared QMD history from canonical database events only."""
import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from src.market_engine import regular_close_history as c
from src.backend.backtest_market_data import CertifiedMarketDayPlan
from src.trading_runtime.arte_journal_schema import storage_preflight
from research.mlops.clickhouse import insert_json_each_row
from . import market_day_sql as sql
from .trade_reporting_flags import DELAYED
from .opening_price_reference_producer import _rows, _time


def producer_code_hash():
    from src.market_engine import opening_price_reference
    from . import trade_reporting_flags
    paths = (Path(__file__), Path(c.__file__), Path(sql.__file__),
        Path(opening_price_reference.__file__), Path(trade_reporting_flags.__file__))
    return c.content_hash(tuple((path.name, sha256(path.read_text(encoding='utf-8').encode()).hexdigest())
        for path in paths))


def _permission(client, *, writable):
    if client.execute("SELECT getSetting('readonly')").strip() != ('0' if writable else '1'):
        raise ValueError('Prior-close lane has the wrong read/write setting')
    user = client.execute('SELECT currentUser()').strip()
    grants = client.execute('SHOW GRANTS FINAL').splitlines()
    inserts = set()
    for line in grants:
        match = re.fullmatch(r'GRANT ([A-Z ,]+) ON ([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*) TO ([a-z][a-z0-9_]*)', line.strip())
        privileges = {value.strip() for value in match[1].split(',')} if match else set()
        if match is None or match[4] != user or not privileges <= {'SELECT', 'INSERT'}:
            raise ValueError('Prior-close lane has broad, delegated or unknown grants')
        if 'INSERT' in privileges:
            inserts.add((match[2], match[3]))
    if not user or not writable and inserts:
        raise ValueError('Prior-close reader has unexpected write authority')
    if writable:
        from src.market_engine.opening_price_reference import TABLES as OPENING
        required = {('q_live', table.name) for table in c.TABLES}
        allowed = required | {(table.database, table.name) for table in OPENING}
        if inserts not in (required, allowed):
            raise ValueError('Prior-close writer lacks its explicit shared-table grants')


def _validate_prices(scope, values):
    low = int(scope.regular.opened_at.timestamp() * 1000000)
    high = int(scope.regular.closed_at.timestamp() * 1000000)
    if scope.regular.closed_at >= scope.cutoff:
        raise ValueError('Prior-close source period must precede its consumer cutoff')
    for row in values:
        if (row['prior_session_date'] != scope.regular.day or row['content_hash'] !=
                c.content_hash({k: v for k, v in row.items() if k != 'content_hash'})):
            raise ValueError('Prior-close value lineage differs')
        if row['canonical_event_count'] and (row['next_ordinal'] < row['canonical_event_count']
                or row['last_ordinal'] + 1 != row['next_ordinal']):
            raise ValueError('Prior-close canonical daily ordinal interval differs')
        if row['closing_bar_count']:
            if (row['prior_close_int'] is None or row['prior_close_int'] <= 0
                    or row['close_sip_timestamp_us'] is None or not low <= row['close_sip_timestamp_us'] < high
                    or row['close_ordinal'] is None or row['canonical_event_count'] == 0
                    or row['closing_bar_count'] > row['canonical_event_count']
                    or not row['next_ordinal'] - row['canonical_event_count'] <= row['close_ordinal'] <= row['last_ordinal']
                    or not row['first_sip_timestamp_us'] <= row['close_sip_timestamp_us'] <= row['last_sip_timestamp_us']):
                raise ValueError('Prior-close price escapes its prior RTH source clock')
        elif any(row[key] is not None for key in ('prior_close_int', 'close_sip_timestamp_us', 'close_ordinal')):
            raise ValueError('Covered-empty prior-close has fabricated price/cursor evidence')


@dataclass(frozen=True)
class PriorCloseInput:
    market: CertifiedMarketDayPlan
    canonical_storage_policy: str
    canonical_disk: str
    producer_code_hash: str
    consumer_identity_plan_hash: str
    population_reference_hash: str
    consumer_rules: tuple

    def __post_init__(self):
        if type(self.market) is not CertifiedMarketDayPlan or len(self.market.sessions) != 1:
            raise ValueError('Prior-close producer requires a certified single-session consumer')
        for value in (self.canonical_storage_policy, self.canonical_disk):
            sql.identifier(value)
            if value.lower() in ('default', 'hdd'):
                raise ValueError('Prior-close canonical storage must be explicitly designated')
        self.scope
        c.require_hash(self.consumer_identity_plan_hash)
        c.require_hash(self.population_reference_hash)
        if (type(self.consumer_rules) is not tuple or not 1 <= len(self.consumer_rules) <= 4096
                or any(type(row) is not tuple or len(row) != 5 for row in self.consumer_rules)
                or tuple(sorted(self.consumer_rules)) != self.consumer_rules
                or len({row[0] for row in self.consumer_rules}) != len(self.consumer_rules)):
            raise ValueError('Prior-close input requires its complete immutable consumer rules')
        for row in self.consumer_rules:
            for value in row:
                if type(value) is not int or not 0 <= value < 2**64:
                    raise ValueError('Prior-close consumer rule is not an unsigned scalar')
            if any(value not in (0,1) for value in row[2:]):
                raise ValueError('Prior-close consumer rule eligibility must be binary')

    @property
    def scope(self):
        from datetime import date
        return c.PriorCloseScope(self.market.build_id, date.fromisoformat(self.market.sessions[0]),
            self.market.token, self.market.tickers, self.producer_code_hash)


def certified_prior_close_input(reader, market, *, canonical_storage_policy, canonical_disk):
    from dataclasses import asdict
    from src.backend.backtest_strategy_one_identity import certify_identity_plan
    from src.trading_runtime.historical_reference_identity import load_reference_pin
    from .opening_price_reference_producer import _time
    if type(market) is not CertifiedMarketDayPlan or len(market.sessions) != 1:
        raise ValueError('Prior-close preparation requires its native consumer plan')
    identity = certify_identity_plan(market, client=reader)
    pin = load_reference_pin(reader, market)
    from datetime import date
    scope = c.PriorCloseScope(market.build_id, date.fromisoformat(market.sessions[0]),
        market.token, market.tickers, producer_code_hash())
    if (_time(pin.cutoff_at) != scope.cutoff or _time(pin.available_at) >= scope.cutoff
            or identity.market_token != market.token):
        raise ValueError('Prior-close consumer identity or reference exceeds its causal cutoff')
    rules = _rows(reader, 'SELECT token_id,modifier_int,update_high_low,update_last,update_volume '
        f'FROM arte.market_day_source_rule_v1 WHERE build_id={sql.literal(market.build_id)} ORDER BY ordinal LIMIT 4097', bound=4097)
    fields = ('token_id','modifier_int','update_high_low','update_last','update_volume')
    if not rules or len(rules) >= 4097 or any(set(row) != set(fields) for row in rules):
        raise ValueError('Prior-close consumer lacks a complete frozen market-rule source')
    frozen = tuple(tuple(c.unsigned(row[key]) for key in fields) for row in rules)
    return PriorCloseInput(market, canonical_storage_policy, canonical_disk, scope.producer_code_hash,
        identity.token, c.content_hash(asdict(pin)), frozen)


def _verify_consumer(reader, source):
    actual = certified_prior_close_input(reader, source.market,
        canonical_storage_policy=source.canonical_storage_policy, canonical_disk=source.canonical_disk)
    if actual != source:
        raise ValueError('Prior-close consumer market, identity or population changed')


def _prefix(scope, attempt):
    return dict(consumer_build_id=scope.consumer_build_id,
        consumer_session_date=scope.consumer_session_date, attempt_id=attempt)


def _where(scope, attempt=None):
    text = (f'consumer_build_id={sql.literal(scope.consumer_build_id)} '
        f'AND consumer_session_date=toDate({sql.literal(scope.consumer_session_date)})')
    return text + (f' AND attempt_id=toUUID({sql.literal(attempt)})' if attempt else '')


def _wire(table, records):
    from datetime import date
    output = []
    for row in records:
        row = c.restore(table, row)
        output.append({key: value.isoformat() if type(value) is date else
            value.strftime('%Y-%m-%d %H:%M:%S.%f') if isinstance(value, datetime) else value
            for key, value in row.items()})
    return output


def _children(reader, scope, table, attempt):
    output = []
    if table == c.RULES:
        packets = [(None, 4097)]
    else:
        packets = [(scope.tickers[i:i + 128], 129) for i in range(0, len(scope.tickers), 128)]
    for tickers, bound in packets:
        extra = ' AND ticker IN (' + ','.join(map(sql.literal, tickers)) + ')' if tickers else ''
        order = 'ticker' if table == c.VALUES else 'token_id'
        rows = _rows(reader, f'SELECT * FROM q_live.{table.name} WHERE {_where(scope, attempt)}'
            f'{extra} ORDER BY {order} LIMIT {bound}', bound=bound)
        if len(rows) >= bound:
            raise ValueError('Prior-close children exceed their bounded source scope')
        output.extend(c.restore(table, row) for row in rows)
    return tuple(output)


def load_published_prior_close(reader, market, *, expected_producer_code_hash, expected_content_hash):
    """Reuse a sealed producing revision with explicit previously verified pins.

    A consumer's newer code is not the historical producer identity. This is
    read-only and cannot publish, regenerate or adopt an unsealed attempt.
    """
    from datetime import date
    if type(market) is not CertifiedMarketDayPlan or len(market.sessions) != 1:
        raise ValueError('Published prior-close read requires one certified consumer session')
    c.require_hash(expected_content_hash)
    scope = c.PriorCloseScope(market.build_id, date.fromisoformat(market.sessions[0]),
        market.token, market.tickers, expected_producer_code_hash)
    result = certify_prior_close(reader, scope, expected_hash=expected_content_hash)
    result.verify_consumer(market)
    return result


def certify_prior_close(reader, scope, *, expected_hash=None):
    if type(scope) is not c.PriorCloseScope:
        raise TypeError('Prior-close certification requires a typed consumer scope')
    storage_preflight(reader, tables=c.TABLES)
    headers = _rows(reader, f'SELECT * FROM q_live.{c.COVERAGE.name} WHERE {_where(scope)} LIMIT 2', bound=2)
    if len(headers) != 1:
        raise ValueError('Prior-close history requires exactly one publication seal')
    header = c.restore(c.COVERAGE, headers[0])
    if (header['consumer_build_id'] != scope.consumer_build_id
            or header['consumer_session_date'] != scope.consumer_session_date
            or header['revision'] != c.REVISION or header['consumer_market_plan_hash'] != scope.consumer_market_plan_hash
            or header['producer_code_hash'] != scope.producer_code_hash
            or header['consumer_cutoff'] != scope.cutoff or header['prior_session_date'] != scope.regular.day
            or header['ticker_count'] != len(scope.tickers)
            or header['content_hash'] != c.content_hash({k: v for k, v in header.items()
                if k not in ('content_hash', 'certified_at')})
            or expected_hash is not None and header['content_hash'] != expected_hash):
        raise ValueError('Prior-close seal differs from its consumer/source pins')
    attempt = header['attempt_id']
    values, rules = (_children(reader, scope, table, attempt) for table in (c.VALUES, c.RULES))
    if (tuple(row['ticker'] for row in values) != scope.tickers or len(rules) != header['rule_count']
            or not rules or len({r['token_id'] for r in rules}) != len(rules)
            or c.content_hash(values) != header['values_hash'] or c.content_hash(rules) != header['rules_hash']):
        raise ValueError('Prior-close complete population or rule children differ')
    for table, rows in ((c.VALUES, values), (c.RULES, rules)):
        key = 'ticker' if table == c.VALUES else 'token_id'
        count = _rows(reader, f'SELECT count() AS n,uniqExact({key}) AS keys FROM q_live.{table.name} '
            f'WHERE {_where(scope, attempt)}', bound=1)
        if len(count) != 1 or int(count[0]['n']) != len(rows) or int(count[0]['keys']) != len(rows):
            raise ValueError('Prior-close children contain extra or duplicate rows')
        if any(any(row[k] != v for k, v in _prefix(scope, attempt).items()) for row in rows):
            raise ValueError('Prior-close child crosses its consumer/attempt scope')
    _validate_prices(scope, values)
    resolved = sum(row['closing_bar_count'] > 0 for row in values)
    if (resolved != header['resolved_count'] or len(values) - resolved != header['covered_empty_count']
            or scope.regular.closed_at >= scope.cutoff):
        raise ValueError('Prior-close coverage counts or causal cutoff differ')
    return c.CertifiedPriorClosePlan(scope, header, values, c._PROOF)


def prior_events_sql(scope, tickers, rules):
    if (type(scope) is not c.PriorCloseScope or type(tickers) is not tuple
            or not 1 <= len(tickers) <= 128 or len(set(tickers)) != len(tickers)
            or not set(tickers) <= set(scope.tickers)):
        raise ValueError('Prior-close event query requires a bounded consumer listing scope')
    _, eligible_last, _, _ = sql.condition_expressions(rules)
    return f'''WITH decoded AS (
      SELECT ticker,sip_timestamp_us,ordinal,bitAnd(event_meta,1) AS kind,event_meta,
        toUInt64(price_primary_int)*if(bitAnd(event_meta,2)!=0,1,100) AS price_int,
        toFloat64(size_primary) AS size,
        fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York') AS local_time,
        toHour(local_time)*3600+toMinute(local_time)*60+toSecond(local_time) AS local_second,
        arrayFilter(t->t>0,[toUInt16(condition_token_1),toUInt16(condition_token_2),
          toUInt16(condition_token_3),toUInt16(condition_token_4),toUInt16(condition_token_5)]) AS tokens,
        0 AS form_ok
      FROM market_sip_compact.events_{scope.regular.day.year}
      WHERE event_date=toDate({sql.literal(scope.regular.day)})
        AND ticker IN ({','.join(map(sql.literal, tickers))})
        AND sip_timestamp_us>={int(scope.regular.opened_at.timestamp()*1000000)}
        AND sip_timestamp_us<{int(scope.regular.closed_at.timestamp()*1000000)}
    ) SELECT ticker,uniqExact(intDiv(sip_timestamp_us,1000000)) AS closing_bar_count,
      argMax(price_int,tuple(sip_timestamp_us,ordinal)) AS prior_close_int,
      argMax(sip_timestamp_us,tuple(sip_timestamp_us,ordinal)) AS close_sip_timestamp_us,
      argMax(ordinal,tuple(sip_timestamp_us,ordinal)) AS close_ordinal
    FROM decoded WHERE kind=1 AND bitAnd(event_meta,{DELAYED})=0
      AND price_int>0 AND size>0 AND isFinite(size) AND {eligible_last}
    GROUP BY ticker ORDER BY ticker LIMIT 129'''


def materialize_prior_close(canonical, source, attempt):
    if source.producer_code_hash != producer_code_hash():
        raise ValueError('Prior-close producer differs from its reviewed source closure')
    scope, day = source.scope, source.scope.regular.day
    table = 'events_' + str(day.year)
    metadata = _rows(canonical, "SELECT name,storage_policy FROM system.tables WHERE database='market_sip_compact' "
        f'AND name={sql.literal(table)}', bound=1)
    disks = _rows(canonical, 'SELECT disks FROM system.storage_policies WHERE policy_name=' +
        sql.literal(source.canonical_storage_policy), bound=8)
    parts = _rows(canonical, "SELECT DISTINCT disk_name FROM system.parts WHERE active AND database='market_sip_compact' "
        f'AND table={sql.literal(table)}', bound=8)
    if (metadata != [dict(name=table, storage_policy=source.canonical_storage_policy)] or not disks
            or any(row['disks'] != [source.canonical_disk] for row in disks)
            or any(row['disk_name'] != source.canonical_disk for row in parts)):
        raise ValueError('Prior-close canonical table policy or actual placement differs')
    stats = _rows(canonical, 'SELECT stats_version,source_filter_key,total_event_rows_after_filters,updated_at '
        f'FROM market_sip_compact.events_source_day_stats FINAL WHERE source_date=toDate({sql.literal(day)})', bound=2)
    inventory = _rows(canonical, 'SELECT ticker,event_count,next_ordinal,last_ordinal,first_sip_timestamp_us,'
        'last_sip_timestamp_us,build_step,updated_at FROM market_sip_compact.events_ordinal_continuity FINAL '
        f'WHERE source_date=toDate({sql.literal(day)}) ORDER BY ticker LIMIT 100001', bound=100001)
    if (len(stats) != 1 or set(stats[0]) != {'stats_version','source_filter_key','total_event_rows_after_filters','updated_at'}
            or any(set(row) != {'ticker','event_count','next_ordinal','last_ordinal','first_sip_timestamp_us',
                'last_sip_timestamp_us','build_step','updated_at'} for row in inventory)):
        raise ValueError('Prior-close canonical source metadata schema differs')
    stats[0]['stats_version'] = c.unsigned(stats[0]['stats_version'], bits=32)
    stats[0]['total_event_rows_after_filters'] = c.unsigned(stats[0]['total_event_rows_after_filters'])
    stats[0]['updated_at'] = _time(stats[0]['updated_at'])
    for row in inventory:
        for key in ('event_count','next_ordinal','last_ordinal','first_sip_timestamp_us','last_sip_timestamp_us','build_step'):
            row[key] = c.unsigned(row[key])
        row['updated_at'] = _time(row['updated_at'])
    if (not inventory or len(inventory) >= 100001 or len({row['ticker'] for row in inventory}) != len(inventory)
            or int(stats[0]['stats_version']) < 1 or not stats[0]['source_filter_key']
            or sum(int(row['event_count']) for row in inventory) != int(stats[0]['total_event_rows_after_filters'])
            # Ordinals span source days. Native daily coverage begins at
            # next_ordinal-event_count; it does not reset to zero each day.
            or any(int(row['next_ordinal']) < int(row['event_count'])
                or int(row['last_ordinal']) + 1 != int(row['next_ordinal'])
                or int(row['first_sip_timestamp_us']) > int(row['last_sip_timestamp_us']) for row in inventory)):
        raise ValueError('Prior-close canonical complete-day inventory differs from certification')
    rules = _rows(canonical, 'SELECT token_id,modifier_int,update_high_low,update_last,update_volume '
        "FROM market_sip_compact.event_condition_token_reference WHERE source_family='trade_conditions' "
        'AND is_join_canonical=1 ORDER BY token_id LIMIT 4097', bound=4097)
    if not rules or len(rules) >= 4097:
        raise ValueError('Prior-close rules are missing or exceed their native bound')
    rule_rows = tuple(c.restore(c.RULES, {**_prefix(scope, attempt), **row}) for row in rules)
    if len({row['token_id'] for row in rule_rows}) != len(rule_rows):
        raise ValueError('Prior-close condition rules are ambiguous')
    if any(row[key] not in (0, 1) for row in rule_rows for key in ('update_high_low','update_last','update_volume')):
        raise ValueError('Prior-close condition eligibility must be binary')
    normalized_rules = [{name: row[name] for name in ('token_id','modifier_int','update_high_low','update_last','update_volume')}
        for row in rule_rows]
    if tuple(tuple(row[key] for key in ('token_id','modifier_int','update_high_low','update_last','update_volume'))
            for row in normalized_rules) != source.consumer_rules:
        raise ValueError('Prior-close condition rules differ from the immutable consumer market recipe')
    by_ticker = {row['ticker']: row for row in inventory}
    values = []
    for offset in range(0, len(scope.tickers), 128):
        batch = scope.tickers[offset:offset + 128]
        queried = _rows(canonical, prior_events_sql(scope, batch, normalized_rules), bound=129)
        if (len(queried) >= 129 or len({row['ticker'] for row in queried}) != len(queried)
                or not {row['ticker'] for row in queried} <= set(batch)
                or any(set(row) != {'ticker','closing_bar_count','prior_close_int','close_sip_timestamp_us','close_ordinal'}
                    for row in queried)):
            raise ValueError('Prior-close canonical price projection is ambiguous or out of scope')
        prices = {row['ticker']: row for row in queried}
        for ticker in batch:
            unit, price = by_ticker.get(ticker), prices.get(ticker)
            row = dict(**_prefix(scope, attempt), ticker=ticker, prior_session_date=day,
                canonical_event_count=unit['event_count'] if unit else 0,
                next_ordinal=unit['next_ordinal'] if unit else 0,
                last_ordinal=unit['last_ordinal'] if unit else 0,
                first_sip_timestamp_us=unit['first_sip_timestamp_us'] if unit else 0,
                last_sip_timestamp_us=unit['last_sip_timestamp_us'] if unit else 0,
                source_build_step=unit['build_step'] if unit else 0,
                source_updated_at=unit['updated_at'] if unit else None,
                closing_bar_count=c.unsigned(price['closing_bar_count']) if price else 0,
                prior_close_int=c.unsigned(price['prior_close_int']) if price else None,
                close_sip_timestamp_us=c.unsigned(price['close_sip_timestamp_us']) if price else None,
                close_ordinal=c.unsigned(price['close_ordinal']) if price else None)
            row['content_hash'] = c.content_hash(row)
            values.append(c.restore(c.VALUES, row))
    header = dict(**_prefix(scope, attempt), revision=c.REVISION,
        consumer_market_plan_hash=scope.consumer_market_plan_hash, consumer_cutoff=scope.cutoff,
        consumer_identity_plan_hash=source.consumer_identity_plan_hash,
        consumer_population_reference_hash=source.population_reference_hash,
        prior_session_date=day, canonical_storage_policy=source.canonical_storage_policy, canonical_disk=source.canonical_disk,
        stats_version=int(stats[0]['stats_version']), source_filter_key=stats[0]['source_filter_key'],
        total_event_rows_after_filters=int(stats[0]['total_event_rows_after_filters']), source_updated_at=stats[0]['updated_at'],
        source_inventory_hash=c.content_hash(inventory), calculation_source_hash=sha256(Path(sql.__file__).read_bytes()).hexdigest(),
        producer_code_hash=source.producer_code_hash, ticker_count=len(values),
        resolved_count=sum(row['closing_bar_count'] > 0 for row in values),
        covered_empty_count=sum(row['closing_bar_count'] == 0 for row in values), rule_count=len(rule_rows),
        values_hash=c.content_hash(tuple(values)), rules_hash=c.content_hash(rule_rows))
    header['content_hash'] = c.content_hash(header)
    _validate_prices(scope, values)
    return tuple(values), rule_rows, header


def publish_prior_close(reader, canonical, writer, source, *, ownership):
    """No schema creation or retry; source recheck and coverage-last exact seal."""
    if type(source) is not PriorCloseInput or len({id(reader), id(canonical), id(writer)}) != 3:
        raise ValueError('Prior-close producer requires distinct owned native lanes')
    if not getattr(reader, 'base_url', None) or any(getattr(lane, 'base_url', None) != reader.base_url for lane in (canonical, writer)):
        raise ValueError('Prior-close lanes must share their database authority')
    ownership.assert_owner()
    _permission(reader, writable=False)
    _permission(canonical, writable=False)
    _permission(writer, writable=True)
    if writer.execute('SELECT currentUser()').strip() in {
            reader.execute('SELECT currentUser()').strip(), canonical.execute('SELECT currentUser()').strip()}:
        raise ValueError('Prior-close publication must use a distinct writer principal')
    storage_preflight(reader, tables=c.TABLES)
    _verify_consumer(reader, source)
    scope = source.scope
    existing = _rows(reader, f'SELECT * FROM q_live.{c.COVERAGE.name} WHERE {_where(scope)} LIMIT 2', bound=2)
    if existing:
        pinned = certify_prior_close(reader, scope)
        values, rules, header = materialize_prior_close(canonical, source, pinned.header['attempt_id'])
        if ({k: v for k, v in pinned.header.items() if k != 'certified_at'} != header
                or tuple(dict(row) for row in pinned.records) != values):
            raise ValueError('Existing prior-close seal differs from its current canonical parents')
        ownership.assert_owner()
        _verify_consumer(reader, source)
        return pinned
    for table in c.TABLES:
        if _rows(reader, f'SELECT attempt_id FROM q_live.{table.name} WHERE {_where(scope)} LIMIT 1', bound=1):
            raise ValueError('Existing prior-close publication requires cold verification or reconciliation')
    attempt = str(uuid4())
    values, rules, header = materialize_prior_close(canonical, source, attempt)
    for table, rows in ((c.RULES, rules), (c.VALUES, values)):
        for offset in range(0, len(rows), 128):
            ownership.assert_owner()
            insert_json_each_row(writer, 'q_live', table.name, [n for n, _ in table.columns], _wire(table, rows[offset:offset+128]))
        if _children(reader, scope, table, attempt) != rows:
            raise ValueError('Prior-close inserted children differ on exact readback')
    if materialize_prior_close(canonical, source, attempt) != (values, rules, header):
        raise ValueError('Prior-close canonical source changed before its seal')
    _verify_consumer(reader, source)
    ownership.assert_owner()
    header['certified_at'] = datetime.now(timezone.utc)
    insert_json_each_row(writer, 'q_live', c.COVERAGE.name, [n for n, _ in c.COVERAGE.columns], _wire(c.COVERAGE, (header,)))
    result = certify_prior_close(reader, scope, expected_hash=header['content_hash'])
    ownership.assert_owner()
    return result


async def publish_prior_close_async(reader_factory, canonical_factory, writer_factory, source, *, ownership):
    from src.backend.qmd_columnar_history_transport import _owned_worker
    def run():
        clients = []
        try:
            for factory in (reader_factory, canonical_factory, writer_factory):
                clients.append(factory())
            return publish_prior_close(*clients, source, ownership=ownership)
        finally:
            failures = []
            for client in reversed(clients):
                try: client.close()
                except Exception as error: failures.append(error)
            if failures:
                raise ExceptionGroup('Prior-close lane cleanup failed', failures)
    return await _owned_worker(run)
