"""Opening identity proof from existing normalized Reference Gateway facts.

This is an explicit producer basis, not a ticker-event repair or a consumer
fallback. All reads retain the exact certified listing and broker identity.
"""
from datetime import date, datetime, timezone

from .market_day_sql import literal
from src.market_engine import opening_price_reference as c

BASIS = 'shared-reference-routing-tradability-v1'
TABLES = ('security_routing_fact_v1', 'security_tradability_fact_v1',
          'feature_tradable_universe_v1')
ROUTING = ('routing_fact_id', 'symbol_id', 'listing_id', 'security_id',
    'provider_ticker', 'ibkr_conid', 'broker', 'sec_type', 'currency_code',
    'routing_status', 'valid_from_utc',
    'valid_to_utc', 'source_table', 'source_event_id', 'source_evidence_ref',
    'source_content_sha256', 'source_run_id', 'inserted_at')
TRADABILITY = ('tradability_fact_id', 'symbol_id', 'listing_id', 'security_id',
    'provider_ticker', 'effective_at_utc', 'observed_at_utc', 'is_tradable',
    'block_status', 'source_table', 'source_event_id', 'source_evidence_ref',
    'source_content_sha256', 'source_run_id', 'inserted_at')
RETAINED = ('universe_date', 'ticker', 'symbol_id', 'listing_id', 'security_id',
    'ibkr_conid', 'is_tradable', 'source_run_id', 'inserted_at')


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00')) if type(value) is str else value
    if not isinstance(result, datetime):
        raise ValueError('Reference basis requires a source timestamp')
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def _tuple(row, ticker_key):
    return tuple(row.get(k) for k in (ticker_key, 'symbol_id', 'listing_id', 'security_id'))


def _latest(rows, field, at):
    eligible = [row for row in rows if stamp(row[field]) < at]
    if not eligible:
        raise ValueError('Shared reference state is missing at the required clock')
    newest = max(stamp(row[field]) for row in eligible)
    selected = [row for row in eligible if stamp(row[field]) == newest]
    if len(selected) != 1:
        raise ValueError('Shared reference state is ambiguous at its latest clock')
    return selected[0]


def resolve_listing(fact, routing, tradability, retained, *, day, regular, cutoff):
    """Require dated native captures plus exact, unblocked broker routing."""
    identity = _tuple(fact, 'ticker')
    expected_conid = str(fact['ibkr_conid'])
    if not expected_conid.isdecimal() or int(expected_conid) <= 0:
        raise ValueError('Shared reference identity lacks its certified broker conid')
    for rows, columns, ticker_key in ((routing, ROUTING, 'provider_ticker'),
            (tradability, TRADABILITY, 'provider_ticker'), (retained, RETAINED, 'ticker')):
        if any(set(row) != set(columns) or _tuple(row, ticker_key) != identity for row in rows):
            raise ValueError('Shared reference source escaped its exact listing identity')
    current = [row for row in retained if str(row['universe_date']) == day.isoformat()]
    previous = [row for row in retained if str(row['universe_date']) == regular.day.isoformat()]
    if len(current) != 1 or len(previous) != 1:
        raise ValueError('Shared reference needs unique retained prior/current listing captures')
    for row, boundary in ((current[0], cutoff), (previous[0], regular.opened_at)):
        if (str(row['ibkr_conid']) != expected_conid or type(row['is_tradable']) not in (int, str)
                or row['is_tradable'] not in (1, '1')
                or stamp(row['inserted_at']) >= boundary):
            raise ValueError('Retained listing capture is late, blocked or has another broker conid')
    if (current[0]['source_run_id'] != fact['source_run_id']
            or stamp(current[0]['inserted_at']) != stamp(fact['source_inserted_at'])):
        raise ValueError('Retained current capture differs from the certified identity')
    if any(stamp(row['inserted_at']) >= cutoff for row in routing + tradability):
        raise ValueError('Shared reference evidence was unavailable at the consumer cutoff')
    if any(stamp(row['observed_at_utc']) >= cutoff for row in tradability):
        raise ValueError('Shared tradability evidence was observed after the consumer cutoff')
    route = _latest(routing, 'valid_from_utc', cutoff)
    if (route['routing_status'] != 'valid' or str(route['ibkr_conid']) != expected_conid
            or str(route['broker']).lower() != 'ibkr' or route['sec_type'] != 'STK'
            or route['currency_code'] != 'USD'
            or stamp(route['valid_from_utc']) > regular.opened_at
            or stamp(route['inserted_at']) >= regular.opened_at
            or route['valid_to_utc'] is not None and stamp(route['valid_to_utc']) <= cutoff):
        raise ValueError('Shared routing does not prove the prior RTH and current opening identity')
    for at in (regular.opened_at, cutoff):
        state = _latest(tradability, 'effective_at_utc', at)
        if (type(state['is_tradable']) not in (int, str) or state['is_tradable'] not in (1, '1')
                or state['block_status'] != 'tradable'
                or stamp(state['observed_at_utc']) >= at or stamp(state['inserted_at']) >= at):
            raise ValueError('Shared reference has no timely unblocked tradability state')
    for row in routing + tradability:
        if any(type(row[k]) is not str or not row[k] for k in
                ('source_table', 'source_event_id', 'source_content_sha256', 'source_run_id')):
            raise ValueError('Shared reference provenance is incomplete')
    digest = c.content_hash(dict(basis=BASIS, identity=fact, routing=routing,
        tradability=tradability, retained=retained, prior_day=regular.day, day=day))
    # This proof is deliberately scoped to these two source sessions. It does
    # not turn a current symbol projection into an unbounded alias interval.
    return c.OpeningListing(fact['listing_id'], fact['ticker'], fact['symbol_id'],
        fact['security_id'], 'shared-reference:' + digest, digest, regular.day,
        day.fromordinal(day.toordinal() + 1))


def load_listings(reader, market, facts, *, read_rows):
    day = date.fromisoformat(market.sessions[0])
    regular = c.previous_regular_session(day)
    cutoff = datetime.combine(day, datetime.min.time(), c.NEW_YORK).replace(hour=4).astimezone(timezone.utc)
    listings = []
    for offset in range(0, len(market.tickers), 128):
        names = market.tickers[offset:offset + 128]
        keys = ','.join('(' + ','.join(literal(facts[name][k]) for k in
            ('ticker', 'symbol_id', 'listing_id', 'security_id')) + ')' for name in names)
        packets = []
        for table, columns, ticker_key in ((TABLES[0], ROUTING, 'provider_ticker'),
                (TABLES[1], TRADABILITY, 'provider_ticker'), (TABLES[2], RETAINED, 'ticker')):
            predicate = f'({ticker_key},symbol_id,listing_id,security_id) IN ({keys})'
            if table == TABLES[2]:
                predicate += f' AND universe_date IN (toDate({literal(regular.day.isoformat())}),toDate({literal(day.isoformat())}))'
            else:
                # Late writes remain excluded from as-of state, never backdated.
                predicate += f' AND inserted_at<toDateTime64({literal(cutoff.strftime("%Y-%m-%d %H:%M:%S.%f"))},6,\'UTC\')'
            rows = read_rows(reader, f'SELECT {",".join(columns)} FROM q_live.{table} FINAL WHERE {predicate} '
                f'ORDER BY {ticker_key},{columns[0]} LIMIT 4097', bound=4097)
            if len(rows) > 4096:
                raise ValueError('Shared reference packet exceeds its bounded source contract')
            packets.append(rows)
        for name in names:
            rows = tuple([row for row in packet if row.get(key) == name]
                for packet, key in zip(packets, ('provider_ticker', 'provider_ticker', 'ticker'), strict=True))
            listings.append(resolve_listing(facts[name], *rows, day=day, regular=regular, cutoff=cutoff))
    return tuple(sorted(listings, key=lambda value: value.listing_id))
