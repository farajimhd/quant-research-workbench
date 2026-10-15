"""Exact scalar transport for centrally declared product tables.

Floating-point batches reuse the existing app RowBinary encoder. This changes
transport only: columns, ownership, publication seals and readback stay with
the caller. It provides no DDL, retry, disk journal or new persistence schema.
"""
from datetime import datetime, timezone

from research.mlops.clickhouse import insert_json_each_row, quote_ident
from src.trading_runtime.arte_journal_rowbinary import encode_journal_rows
from src.trading_runtime.arte_journal_schema import TableContract


def insert_product_rows(client, table, rows):
    if type(table) is not TableContract or not rows or len(rows) > 4096:
        raise ValueError('Product INSERT requires a bounded declared table batch')
    if any(set(row) != {name for name, _ in table.columns} for row in rows):
        raise ValueError('Product INSERT differs from its exact scalar schema')
    if any('Float64' in kind or 'Float32' in kind for _, kind in table.columns):
        header = (f'INSERT INTO {quote_ident(table.database)}.{quote_ident(table.name)} '
            f'({",".join(quote_ident(name) for name, _ in table.columns)}) FORMAT RowBinary\n')
        request = header.encode() + encode_journal_rows(tuple(table.columns), tuple(rows))
        if len(request) > 16 * 1024 * 1024:
            raise ValueError('Product binary INSERT exceeds its finite byte budget')
        client.execute(request)
    else:
        # The JSON transport's basic DateTime parser does not accept offsets.
        # Normalize aware Python instants to the table's declared UTC wire form.
        # Existing canonical UTC strings remain unchanged; naive objects fail.
        timestamps = {name for name, kind in table.columns if 'DateTime64' in kind}
        wire = []
        for row in rows:
            projected = dict(row)
            for name in timestamps:
                value = projected[name]
                if isinstance(value, datetime):
                    if value.tzinfo is None or value.utcoffset() is None:
                        raise ValueError('Product timestamp requires an explicit timezone')
                    projected[name] = value.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
            wire.append(projected)
        insert_json_each_row(client, table.database, table.name,
            [name for name, _ in table.columns], wire)
