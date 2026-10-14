"""Decode product transport in protocol fakes; production uses server decoding."""
from datetime import date, datetime, timedelta, timezone
import json
import struct
from uuid import UUID
from decimal import Decimal, localcontext
import re


def product_request_as_json(request, contracts):
    header, _, payload = request.partition(b'\n')
    text = header.decode()
    table = next(t for t in contracts if f'`{t.database}`.`{t.name}`' in text)
    position = 0
    def number(fmt):
        nonlocal position
        size = struct.calcsize(fmt)
        value, = struct.unpack_from(fmt, payload, position)
        position += size
        return value
    def value(kind):
        nonlocal position
        if kind.startswith('Nullable('):
            return None if number('<B') else value(kind[9:-1])
        if kind in ('String', 'LowCardinality(String)'):
            length, shift = 0, 0
            while True:
                byte = number('<B'); length |= (byte & 127) << shift
                if byte < 128: break
                shift += 7
            result = payload[position:position+length].decode(); position += length
            return result
        if kind == 'FixedString(64)':
            result = payload[position:position+64].decode(); position += 64
            return result
        if kind == 'UUID': return str(UUID(int=(number('<Q') << 64) | number('<Q')))
        if kind == 'Date': return (date(1970,1,1) + timedelta(days=number('<H'))).isoformat()
        if kind == "DateTime64(6, 'UTC')":
            return (datetime(1970,1,1,tzinfo=timezone.utc) + timedelta(microseconds=number('<q'))).isoformat()
        decimal = re.fullmatch(r'Decimal\((\d+),\s*(\d+)\)',kind)
        if decimal:
            precision,scale=map(int,decimal.groups())
            width=4 if precision<=9 else 8 if precision<=18 else 16 if precision<=38 else 32
            integer=int.from_bytes(payload[position:position+width],'little',signed=True);position+=width
            with localcontext() as context:
                context.prec=100
                return str(Decimal(integer).scaleb(-scale))
        formats = {'Float32':'<f', 'Float64':'<d', 'UInt8':'<B', 'UInt16':'<H', 'UInt32':'<I',
                   'UInt64':'<Q', 'Int32':'<i', 'Int64':'<q', 'Bool':'<?'}
        if kind not in formats: raise AssertionError('Unsupported fixture type '+kind)
        return number(formats[kind])
    rows=[]
    while position < len(payload):
        rows.append({name:value(kind) for name,kind in table.columns})
    assert position == len(payload)
    return text.replace('FORMAT RowBinary','FORMAT JSONEachRow')+'\n'+'\n'.join(json.dumps(r) for r in rows)
