"""Additive, restart-safe session-boundary extension of the existing ARTE tables."""
from research.level_book.v7.clickhouse_persistence import (
    LEVELS_TABLE, OBSERVATIONS_TABLE, COVERAGE_TABLE, POLICY, encode_rows,
)
from pipelines.market_sip.events.market_day_sql import literal

KEYS = {LEVELS_TABLE: 'ticker, level_id, valid_from',
        OBSERVATIONS_TABLE: 'ticker, observation_id, valid_from',
        COVERAGE_TABLE: 'ticker, session_date'}


def storage_preflight(client):
    policies = client.query(f"SELECT disks FROM system.storage_policies WHERE policy_name={literal(POLICY)}")
    allowed = {disk for row in policies for disk in row['disks']}
    if not allowed or allowed.intersection({'default', 'hdd'}):
        raise ValueError('Structural checkpoints require an SSD-only explicit policy')
    for table in KEYS:
        name = table.split('.')[1]
        info = client.query(f"SELECT storage_policy,sorting_key FROM system.tables WHERE database='arte' AND name={literal(name)}")
        parts = client.query(f"SELECT DISTINCT disk_name FROM system.parts WHERE active AND database='arte' AND table={literal(name)}")
        if len(info) != 1 or info[0]['storage_policy'] != POLICY or any(p['disk_name'] not in allowed for p in parts):
            raise ValueError(f'Absent or misplaced structural table: {table}')
        if info[0]['sorting_key'] not in (KEYS[table], KEYS[table] + ', checkpoint_boundary'):
            raise ValueError(f'Unexpected structural ordering: {table}')


def migrate(client):
    """Append a new implicit-default key; old rows remain AH (empty string).

    ADD and MODIFY must be one ALTER: ClickHouse requires the new sorting-key
    coordinate to be a newly added column without an explicit default. No old
    parts are rewritten or deleted. Partial table migration can safely resume.
    """
    storage_preflight(client)
    for table, key in KEYS.items():
        cols = client.query(f"SELECT name,type FROM system.columns WHERE database='arte' AND table={literal(table.split('.')[1])}")
        columns = {row['name']: row['type'] for row in cols}
        if 'checkpoint_boundary' not in columns:
            client.http.execute(f'ALTER TABLE {table} ADD COLUMN checkpoint_boundary LowCardinality(String), '
                                f'MODIFY ORDER BY ({key}, checkpoint_boundary)')
        elif columns['checkpoint_boundary'] != 'LowCardinality(String)':
            raise ValueError('Incompatible structural boundary column')
        if table == COVERAGE_TABLE:
            client.http.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS streaming_state String CODEC(ZSTD(3)), '
                                'ADD COLUMN IF NOT EXISTS streaming_state_hash String')
    require_schema(client)


def require_schema(client):
    storage_preflight(client)
    for table, key in KEYS.items():
        info = client.query(f"SELECT sorting_key FROM system.tables WHERE database='arte' AND name={literal(table.split('.')[1])}")
        if info[0]['sorting_key'] != key + ', checkpoint_boundary':
            raise ValueError('Session checkpoint schema migration is required before writing')


def boundary_key(ticker, day, boundaries=('PM', 'REGULAR')):
    """One PREWHERE step must filter the entire key before reading large states.

    Separate AND predicates can make ClickHouse's multi-step PREWHERE read a
    whole ticker's payload granule before applying the date/boundary filters.
    Tuple IN remains one filter while preserving primary-key pruning and FINAL.
    """
    if not boundaries or any(value not in ('PM', 'REGULAR') for value in boundaries):
        raise ValueError('Expected intraday checkpoint boundaries')
    values = ','.join(f'({literal(ticker)},toDate({literal(day)}),{literal(value)})'
                      for value in boundaries)
    return f'(ticker,session_date,checkpoint_boundary) IN [{values}]'


def load_boundary(client, *, ticker, day, as_of):
    """Restore the latest completed intraday state available at the requested cut.

    Callers must separately pin the certified market source; this API does not
    discover a population or substitute an AH state for an intraday checkpoint.
    """
    from .structural_session_checkpoints import restore_boundary
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError('Intraday checkpoint cutoff must be timezone-aware')
    rows = client.query(f"SELECT * FROM {COVERAGE_TABLE} FINAL PREWHERE {boundary_key(ticker, day)} "
        f"WHERE available_at<=parseDateTime64BestEffort({literal(as_of.isoformat())},9,'UTC') "
        "ORDER BY available_at DESC LIMIT 1")
    if not rows:
        return None
    return restore_boundary(rows[0])


def insert(client, table, rows):
    # JSONEachRow explicit names preserve all legacy implicit defaults.
    import struct
    rows = iter(rows)
    first = next(rows, None)
    if first is None:
        return
    from itertools import chain
    rows = chain([first], rows)
    prefix = f'INSERT INTO {table} FORMAT JSONEachRow\n'
    floats = set()
    if table in (LEVELS_TABLE, OBSERVATIONS_TABLE):
        columns = {r['name']: r['type'] for r in client.query(
            "SELECT name,type FROM system.columns WHERE database='arte' "
            f"AND table={literal(table.split('.')[1])}")}
        names = list(first)
        floats = {k for k in names if columns[k] in ('Float64', 'Nullable(Float64)')}
        schema = ','.join(f'`{k}` {columns[k].replace("Float64", "UInt64") if k in floats else columns[k]}'
                          for k in names)
        projections = ','.join(f'reinterpretAsFloat64(`{k}`)' if k in floats else f'`{k}`'
                               for k in names)
        fields = ','.join(f'`{k}`' for k in names)
        # Decimal JSON parsing can change a Float64 by one ULP. Integer bits
        # avoid that conversion while retaining exact snapshot verification.
        prefix = (f'INSERT INTO {table} ({fields}) SELECT {projections} '
                  f'FROM input({literal(schema)}) FORMAT JSONEachRow\n')
    maximum_bytes=140*1024**2 if table==COVERAGE_TABLE else 4*1024**2
    lines=[];size=0
    def flush():
        client.http.execute(prefix+'\n'.join(lines))
    for row in rows:
        if floats:
            row = {k: struct.unpack('<Q', struct.pack('<d', v))[0]
                   if k in floats and v is not None else v for k, v in row.items()}
        line=encode_rows([row]);amount=len(line.encode())+1
        if amount>maximum_bytes:raise ValueError('Structural row exceeds bounded insert payload')
        if lines and (len(lines)>=5000 or size+amount>maximum_bytes):
            flush();lines=[];size=0
        lines.append(line);size+=amount
    if lines:flush()


def verify_snapshot(client, table, expected, where):
    identity = 'level_id' if table == LEVELS_TABLE else 'observation_id'
    actual = client.query(f'SELECT * FROM {table} FINAL PREWHERE {where} '
                          f'ORDER BY {identity} LIMIT {len(expected)+1}')
    ordered = sorted(expected, key=lambda row: row[identity])
    if len(actual) != len(ordered) or any(
            any(row.get(key) != value for key, value in wanted.items())
            for row, wanted in zip(actual, ordered)):
        raise ValueError('Normalized checkpoint snapshot differs from calculated state')


def publish_boundary(client, value, plan_hash):
    from .structural_session_checkpoints import normalized_boundary, restore_boundary
    levels, observations, certificate = normalized_boundary(value, plan_hash)
    where = boundary_key(value['ticker'], value['day'], (value['boundary'],))
    existing = client.query(f'SELECT * FROM {COVERAGE_TABLE} FINAL PREWHERE {where}')
    if existing:
        if len(existing) != 1 or existing[0]['streaming_state_hash'] != certificate['streaming_state_hash']:
            raise ValueError('Existing boundary differs from immutable request')
        restore_boundary(existing[0])
        for table, expected in ((LEVELS_TABLE, levels), (OBSERVATIONS_TABLE, observations)):
            verify_snapshot(client, table, expected,
                f"ticker={literal(value['ticker'])} AND checkpoint_boundary={literal(value['boundary'])} "
                f"AND valid_from=toDateTime64({literal(certificate['available_at'])},9,'UTC')")
        return 'reused'
    insert(client, LEVELS_TABLE, levels)
    insert(client, OBSERVATIONS_TABLE, observations)
    # Verify full normalized snapshots before exposing their completion row.
    stamp = literal(certificate['available_at'])
    for table, expected in ((LEVELS_TABLE, levels), (OBSERVATIONS_TABLE, observations)):
        verify_snapshot(client, table, expected, f"ticker={literal(value['ticker'])} "
            f"AND checkpoint_boundary={literal(value['boundary'])} AND valid_from=toDateTime64({stamp},9,'UTC')")
    insert(client, COVERAGE_TABLE, [certificate])
    published = client.query(f'SELECT * FROM {COVERAGE_TABLE} FINAL PREWHERE {where}')
    if len(published) != 1:
        raise ValueError('Boundary completion read-back failed')
    restore_boundary(published[0])
    storage_preflight(client)
    return 'completed'
