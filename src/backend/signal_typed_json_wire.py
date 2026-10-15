"""ClickHouse basic UTC DateTime64 wire values, separate from logical hashes."""
from datetime import datetime, timezone


def typed_json_wire_row(table, row):
    projected = dict(row)
    for name, kind in table.columns:
        if 'DateTime64' not in kind or projected[name] is None:
            continue
        if "'UTC'" not in kind:
            raise ValueError("Signal timestamp transport requires a declared UTC column")
        raw = projected[name]
        value = raw if isinstance(raw, datetime) else datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Signal timestamp transport requires an aware logical instant")
        projected[name] = value.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
    return projected
