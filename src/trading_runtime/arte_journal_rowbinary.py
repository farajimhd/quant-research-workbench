"""Exact binary encoding of already canonical typed journal batch rows.

This module does not query, insert, hash, or change journal values. The caller
supplies the existing ordered contract and validated wire rows. Float64 values
are transmitted as IEEE 754 bits, without server decimal text conversion.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from functools import lru_cache
import math
import re
import struct
from typing import Any, Mapping
from uuid import UUID


def _varuint(value: int) -> bytes:
    output = bytearray()
    while value >= 128:
        output.append((value & 127) | 128)
        value >>= 7
    output.append(value)
    return bytes(output)


@lru_cache(maxsize=None)
def _encoder(kind: str):
    if kind.startswith('Nullable(') and kind.endswith(')'):
        inner = _encoder(kind[9:-1])
        return lambda value: b'\x01' if value is None else b'\x00' + inner(value)
    if kind in {'String', 'LowCardinality(String)'}:
        def string(value):
            payload = value.encode('utf-8')
            return _varuint(len(payload)) + payload
        return string
    if kind == 'FixedString(64)':
        def fixed(value):
            payload = value.encode('utf-8')
            if len(payload) != 64:
                raise ValueError('Journal FixedString(64) must contain exactly 64 bytes')
            return payload
        return fixed
    if kind == 'Float64':
        def floating(value):
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('Journal Float64 must be finite')
            return struct.pack('<d', value)
        return floating
    formats = {'UInt8': 'B', 'UInt16': 'H', 'UInt32': 'I', 'UInt64': 'Q',
               'Int32': 'i', 'Int64': 'q', 'Bool': '?'}
    if kind in formats:
        packing = struct.Struct('<' + formats[kind])
        return packing.pack
    if kind == 'UUID':
        def uuid(value):
            number = UUID(str(value)).int
            return struct.pack('<QQ', number >> 64, number & ((1 << 64) - 1))
        return uuid
    if kind == 'Date':
        return lambda value: struct.pack('<H', (date.fromisoformat(str(value))
                                               - date(1970, 1, 1)).days)
    if kind == "DateTime64(6, 'UTC')":
        def timestamp(value):
            instant = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            if instant.tzinfo is None:
                raise ValueError('Journal binary timestamp requires an explicit timezone')
            delta = instant.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
            ticks = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
            return struct.pack('<q', ticks)
        return timestamp
    decimal = re.fullmatch(r'Decimal\((\d+),\s*(\d+)\)', kind)
    if decimal:
        precision, scale = map(int, decimal.groups())
        width = 4 if precision <= 9 else 8 if precision <= 18 else 16 if precision <= 38 else 32
        if not 1 <= precision <= 76 or not 0 <= scale <= precision:
            raise ValueError('Invalid journal Decimal contract')
        def decimal_value(value):
            with localcontext() as context:
                context.prec = 100
                number = Decimal(str(value)) * (Decimal(10) ** scale)
                if not number.is_finite() or number != number.to_integral_value():
                    raise ValueError('Journal Decimal loses scale')
                return int(number).to_bytes(width, 'little', signed=True)
        return decimal_value
    raise ValueError(f'Unsupported journal RowBinary type: {kind}')


@lru_cache(maxsize=None)
def _fields(columns: tuple[tuple[str, str], ...]):
    return tuple((name, _encoder(kind)) for name, kind in columns)


def encode_journal_rows(columns: tuple[tuple[str, str], ...],
                        rows: tuple[Mapping[str, Any], ...]) -> bytes:
    """Encode one bounded publication batch in the exact ordered schema.

    Schema dispatch is compiled once. The existing publication batch bounds
    memory; this function performs no per-row database or market-data calls.
    """
    fields = _fields(columns)
    expected = frozenset(name for name, _ in fields)
    output = bytearray()
    for row in rows:
        if row.keys() != expected:
            raise ValueError('Journal RowBinary row differs from its ordered contract')
        for name, encode in fields:
            output.extend(encode(row[name]))
    return bytes(output)
