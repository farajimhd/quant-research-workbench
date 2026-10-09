"""Bounded SELECT response preparation, never market or execution authority.

The complete HTTP message is consumed before any decoded row is returned.
Callers must separately pin certified products, windows and expected coverage,
and expose each row only at its completed clock. No retry or disk spool occurs.
Unselected consumers retain their existing streaming transport.
"""
from dataclasses import dataclass
import json
from math import isfinite
import sys
from urllib import request

from research.mlops.clickhouse import ClickHouseHttpClient
from src.backend.backtest_market_data import assert_select_only


@dataclass(frozen=True, slots=True)
class CompleteMarketResponseBounds:
    max_wire_bytes: int
    max_rows: int
    max_resident_bytes: int

    def __post_init__(self):
        for value, ceiling in ((self.max_wire_bytes, 16 * 1024 * 1024),
                               (self.max_rows, 100000),
                               (self.max_resident_bytes, 256 * 1024 * 1024)):
            if type(value) is not int or not 0 < value <= ceiling:
                raise ValueError('Complete market response bounds require exact bounded integers')


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Complete market response repeated an object field')
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError('Complete market response contains a nonfinite JSON number')


def _resident_size(value, *, remaining, depth=0):
    # Conservative accounting deliberately counts repeated references again.
    # No retained identity ledger or unbounded object graph is constructed.
    if depth > 32:
        raise ValueError('Complete market response exceeds nested value depth')
    size = sys.getsizeof(value)
    if size > remaining:
        raise ValueError('Complete market response exceeds resident byte budget')
    if type(value) is float and not isfinite(value):
        raise ValueError('Complete market response contains a nonfinite JSON number')
    if type(value) is dict:
        children = (part for pair in value.items() for part in pair)
    elif type(value) is list:
        children = iter(value)
    elif type(value) in (str, int, float, bool, type(None)):
        return size
    else:
        raise ValueError('Complete market response contains an unsupported value')
    for child in children:
        size += _resident_size(child, remaining=remaining - size, depth=depth + 1)
    return size


def read_complete_market_response(client, sql, *, bounds):
    """Return rows only after EOF, framing and bounded decoding all succeed.

    Limits are declared by the consumer. This helper does not issue a source
    witness, financial permission, coverage certificate or recovery decision.
    Network and partial-message exceptions propagate without retry.
    """
    if (type(client) is not ClickHouseHttpClient
            or client.user != 'backtest_v3_reader'
            or client.default_query_params.get('readonly') != '1'
            or type(bounds) is not CompleteMarketResponseBounds):
        raise ValueError('Complete market response requires the exact read-only V3 client and bounds')
    sql = assert_select_only(sql)
    if not sql.rstrip().endswith('FORMAT JSONEachRow'):
        raise ValueError('Complete market response requires JSONEachRow')
    req = request.Request(client._request_url(dict(client.default_query_params)),
                          data=sql.encode('utf8'), method='POST')
    req.add_header('X-ClickHouse-User', client.user)
    req.add_header('X-ClickHouse-Key', client.password)
    with request.urlopen(req, timeout=client.timeout_seconds) as response:
        declared = response.headers.get('Content-Length')
        if declared is not None:
            if not declared.isdecimal() or int(declared) > bounds.max_wire_bytes:
                raise ValueError('Complete market response length exceeds its wire budget or is invalid')
        body = response.read(bounds.max_wire_bytes + 1)
        if len(body) > bounds.max_wire_bytes:
            raise ValueError('Complete market response exceeds wire byte budget')
        if declared is not None and len(body) != int(declared):
            raise ValueError('Complete market response ended before declared content length')
        if response.read(1):
            raise ValueError('Complete market response exceeds wire byte budget')
    if body and not body.endswith(b'\n'):
        raise ValueError('Complete market response lacks its final row delimiter')
    rows = []
    # Reserve both accumulation and final tuple references, plus wire and line
    # scratch space. Decoded values are counted conservatively below.
    resident = 2 * len(body) + 128 + bounds.max_rows * 24
    if resident > bounds.max_resident_bytes:
        raise ValueError('Complete market response exceeds resident byte budget')
    offset = 0
    while offset < len(body):
        end = body.index(b'\n', offset)
        line = body[offset:end]
        offset = end + 1
        if not line.strip():
            continue
        if len(rows) == bounds.max_rows:
            raise ValueError('Complete market response exceeds row budget; no truncation allowed')
        value = json.loads(line, object_pairs_hook=_object, parse_constant=_nonfinite)
        if type(value) is not dict:
            raise ValueError('Complete market response contains a non-object row')
        resident += _resident_size(value, remaining=bounds.max_resident_bytes - resident)
        rows.append(value)
    return tuple(rows)
