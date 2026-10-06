"""Bounded SELECT-only normalized entry readback; not financial admission.

The original semantic batch must separately come from verified commit recovery.
Reading these rows establishes source equivalence only. It does not attest the
batch's historical Portfolio/broker predecessor or install a strategy.
"""
import json
from datetime import date

from .arte_declared_native_command_v4 import (
    TABLES, DeclaredEntryRows, _scalar, readback_declared_entry_source_equivalence,
)
from .arte_journal_writer import TypedJournalBatch
from .arte_declared_native_managed_sources import PreparedDeclaredManagedSourceResolver
from src.backend.backtest_market_data import _literal, assert_select_only


_COUNTS = (1, 4, 1, 1, 4)


def _unique_columns(pairs):
    row = {}
    for key, value in pairs:
        if key in row:
            raise ValueError('Declared entry wire has duplicate scalar columns')
        row[key] = value
    return row


def read_declared_entry_rows(base, *, resolver, spec, envelope, approval, market, predecessor):
    """Reload every family in one exact batch; reject extras and omissions.

    Query the batch rather than a caller-chosen row list: foreign siblings are
    errors, including rows that would otherwise be hidden by a parent filter.
    """
    if (type(base) is not TypedJournalBatch
            or type(resolver) is not PreparedDeclaredManagedSourceResolver
            or type(base.run_month) is not date or len(base.events) != 1):
        raise ValueError('Declared entry readback requires exact own source and semantic batch')
    for value in (base.run_id, base.batch_id):
        _scalar(value, 'UUID')
    if base.run_id != resolver.run_id:
        raise ValueError('Declared entry readback crossed run source')
    client = resolver.client
    if client.execute("SELECT getSetting('readonly')").strip() != '1':
        raise ValueError('Declared entry readback requires its SELECT-only source principal')
    families = []
    for contract, count in zip(TABLES, _COUNTS, strict=True):
        columns = ','.join(
            f'toString({name}) AS {name}' if kind in ('UUID', 'Date', 'Decimal(38,18)') else name
            for name, kind in contract.columns)
        ordering = 'ordinal' if any(name == 'ordinal' for name, _ in contract.columns) else 'record_id'
        sql = assert_select_only(
            f'SELECT {columns} FROM arte.{contract.name} '
            f'WHERE run_id={_literal(base.run_id)} AND batch_id=toUUID({_literal(base.batch_id)}) '
            f'ORDER BY {ordering} LIMIT {count + 1} '
            'SETTINGS output_format_json_quote_64bit_integers=0 FORMAT JSONEachRow')
        raw = client.execute(sql)
        if type(raw) is not str:
            raise ValueError('Declared entry readback requires exact JSONEachRow wire output')
        lines = [line for line in raw.splitlines() if line.strip()]
        if len(lines) != count:
            raise ValueError('Declared entry readback family coverage is missing or excessive')
        rows = []
        for line in lines:
            row = json.loads(line, object_pairs_hook=_unique_columns)
            if type(row) is not dict or set(row) != {name for name, _ in contract.columns}:
                raise ValueError('Declared entry readback has missing or extra scalar columns')
            for name, kind in contract.columns:
                _scalar(row[name], kind)
            rows.append(row)
        families.append((contract.name, tuple(rows)))
    packet = DeclaredEntryRows(base, tuple(families))
    readback_declared_entry_source_equivalence(packet, resolver=resolver, spec=spec,
        envelope=envelope, approval=approval, market=market, predecessor=predecessor)
    return packet
