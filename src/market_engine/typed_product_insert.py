"""Exact scalar transport for centrally declared product tables.

Float64-bearing batches reuse the existing app RowBinary encoder. This changes
transport only: columns, ownership, publication seals and readback stay with
the caller. It provides no DDL, retry, disk journal or new persistence schema.
"""
from research.mlops.clickhouse import insert_json_each_row, quote_ident
from src.trading_runtime.arte_journal_rowbinary import encode_journal_rows
from src.trading_runtime.arte_journal_schema import TableContract


def insert_product_rows(client, table, rows):
    if type(table) is not TableContract or not rows or len(rows) > 4096:
        raise ValueError('Product INSERT requires a bounded declared table batch')
    if any(set(row) != {name for name, _ in table.columns} for row in rows):
        raise ValueError('Product INSERT differs from its exact scalar schema')
    if any('Float64' in kind for _, kind in table.columns):
        header = (f'INSERT INTO {quote_ident(table.database)}.{quote_ident(table.name)} '
            f'({",".join(quote_ident(name) for name, _ in table.columns)}) FORMAT RowBinary\n')
        request = header.encode() + encode_journal_rows(tuple(table.columns), tuple(rows))
        if len(request) > 16 * 1024 * 1024:
            raise ValueError('Product binary INSERT exceeds its finite byte budget')
        client.execute(request)
    else:
        insert_json_each_row(client, table.database, table.name,
            [name for name, _ in table.columns], rows)
