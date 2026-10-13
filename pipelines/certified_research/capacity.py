"""Bounded physical ClickHouse storage evidence; never invent projection rates."""
from __future__ import annotations

from .contracts import identifier,literal,digest


def inventory(query,database='arte'):
    identifier(database)
    tables=query('SELECT database,table,sum(rows) rows,sum(data_compressed_bytes) compressed_bytes,'
        'sum(data_uncompressed_bytes) uncompressed_bytes,sum(bytes_on_disk) disk_bytes,count() parts '
        f'FROM system.parts WHERE active AND (database={literal(database)} OR '
        "(database='market_sip_compact' AND match(table,'^events_[0-9]{4}$'))) "
        'GROUP BY database,table ORDER BY database,table')
    disks=query("SELECT name,total_space,free_space,unreserved_space FROM system.disks "
        "WHERE name IN ('live_market_ssd','sip_raw_ssd') ORDER BY name")
    for row in tables:
        for field in ('rows','compressed_bytes','uncompressed_bytes','disk_bytes','parts'):row[field]=int(row[field])
        row['compressed_bytes_per_row']=row['compressed_bytes']/row['rows'] if row['rows'] else None
        row['compression_ratio']=row['uncompressed_bytes']/row['compressed_bytes'] if row['compressed_bytes'] else None
    for row in disks:
        for field in ('total_space','free_space','unreserved_space'):row[field]=int(row[field])
    result=dict(tables=tables,disks=disks,projection_ready=False,
        limitation='Physical inventory only. Campaign projection requires qualified product/session pilot rates, staging and merge headroom; old table rates are not equivalent to new normalized schemas.')
    result['evidence_hash']=digest(result)
    return result
