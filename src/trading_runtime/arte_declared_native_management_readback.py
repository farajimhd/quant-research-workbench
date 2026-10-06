"""Bounded SELECT-only management transport recovery; no financial authority."""
import json

from .arte_declared_native_management_v4 import (
    TABLES, DeclaredManagementRows, readback_declared_management_transport,
)
from .arte_declared_native_command_v4 import _scalar
from .arte_declared_native_entry_readback import _unique_columns
from .arte_declared_native_managed_sources import PreparedDeclaredManagedSourceResolver
from .arte_journal_writer import TypedJournalBatch
from src.backend.backtest_market_data import _literal, assert_select_only


def read_declared_management_rows(bases, *, resolver, **scope):
    """Recover an entire batch, including foreign siblings that must reject.

    Semantic bases must independently come from verified commit recovery.
    This loader provides transport equivalence, never predecessor approval.
    """
    if (type(bases) is not tuple or not 1 <= len(bases) <= 2
            or any(type(base) is not TypedJournalBatch or len(base.events) != 1 for base in bases)
            or type(resolver) is not PreparedDeclaredManagedSourceResolver):
        raise ValueError('Declared management readback requires exact semantic bases and source')
    anchor = bases[0]
    for base in bases:
        _scalar(base.run_id, 'UUID')
        _scalar(base.batch_id, 'UUID')
        if base.run_id != resolver.run_id or (base.run_id, base.batch_id) != (anchor.run_id, anchor.batch_id):
            raise ValueError('Declared management readback crossed run or batch')
    client = resolver.client
    if client.execute("SELECT getSetting('readonly')").strip() != '1':
        raise ValueError('Declared management readback requires SELECT-only source principal')
    families = []
    total_bytes = 0
    for contract in TABLES:
        names = {name for name, _ in contract.columns}
        columns = ','.join(f'toString({name}) AS {name}' if kind in ('UUID', 'Date') else name
                           for name, kind in contract.columns)
        # Restore the declared projection order explicitly, rather than sorting
        # decoded rows and hiding a malformed or unexpected database response.
        ordering = []
        if 'phase' in names:
            ordering.append("multiIf(phase='prior',0,phase='result',1,2)")
        if 'group' in names:
            ordering.append("multiIf(group='ids',0,group='pending_group',1,group='earned_group',2,group='breaks',3,group='overhead',4,5)")
        ordering.append('ordinal' if 'ordinal' in names else 'record_id')
        sql = assert_select_only(f'SELECT {columns} FROM arte.{contract.name} '
            f'WHERE run_id={_literal(anchor.run_id)} AND batch_id=toUUID({_literal(anchor.batch_id)}) '
            f'ORDER BY {",".join(ordering)} LIMIT 65537 '
            "SETTINGS output_format_json_quote_64bit_integers=0, max_result_bytes=16777216, "
            "result_overflow_mode='throw' FORMAT JSONEachRow")
        raw = client.execute(sql)
        if type(raw) is not str:
            raise ValueError('Declared management readback requires exact JSONEachRow output')
        total_bytes += len(raw.encode('utf-8'))
        if total_bytes > 16 * 1024 * 1024:
            raise ValueError('Declared management wire exceeds bounded command inventory')
        lines = [line for line in raw.splitlines() if line.strip()]
        if len(lines) > 65536:
            raise ValueError('Declared management family exceeds row bound')
        rows = []
        for line in lines:
            row = json.loads(line, object_pairs_hook=_unique_columns)
            if type(row) is not dict or set(row) != names:
                raise ValueError('Declared management readback scalar columns differ')
            for name, kind in contract.columns:
                _scalar(row[name], kind)
            rows.append(row)
        families.append((contract.name, tuple(rows)))
    packet = DeclaredManagementRows(bases, tuple(families))
    readback_declared_management_transport(packet, resolver=resolver, **scope)
    return packet
