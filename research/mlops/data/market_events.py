from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from research.mlops.clickhouse_events import EVENT_ROW_DTYPE
from research.mlops.data.contracts import CompactEvent


def event_from_row(row: np.void, *, ticker: str) -> CompactEvent:
    return CompactEvent(
        ticker=ticker.upper(),
        sip_timestamp_us=int(row["sip_timestamp_us"]),
        event_type=int(row["event_type"]),
        price_primary_int=int(row["price_primary_int"]),
        price_secondary_int=int(row["price_secondary_int"]),
        size_primary=float(row["size_primary"]),
        size_secondary=float(row["size_secondary"]),
        exchange_primary=int(row["exchange_primary"]),
        exchange_secondary=int(row["exchange_secondary"]),
        event_flags=int(row["event_flags"]),
        conditions_packed=int(row["conditions_packed"]),
        ordinal=int(row["ordinal"]) if "ordinal" in row.dtype.names else None,
    )


def events_from_rows(rows: np.ndarray, *, ticker: str) -> tuple[CompactEvent, ...]:
    if rows.dtype != EVENT_ROW_DTYPE and not set(EVENT_ROW_DTYPE.names or ()).issubset(rows.dtype.names or ()):
        raise ValueError("rows must contain the unified ClickHouse event dtype columns")
    return tuple(event_from_row(row, ticker=ticker) for row in rows)


def sort_events(events: Iterable[CompactEvent]) -> tuple[CompactEvent, ...]:
    return tuple(sorted(events, key=lambda event: event.sort_key))


def filter_valid_events(events: Iterable[CompactEvent], *, drop_issue_flags: bool = True) -> tuple[CompactEvent, ...]:
    if not drop_issue_flags:
        return tuple(events)
    return tuple(event for event in events if int(event.issue_flags) == 0)


def events_to_rows(events: Iterable[CompactEvent]) -> np.ndarray:
    event_tuple = tuple(events)
    rows = np.zeros((len(event_tuple),), dtype=EVENT_ROW_DTYPE)
    for idx, event in enumerate(event_tuple):
        rows[idx]["span_id"] = 0
        rows[idx]["ordinal"] = 0 if event.ordinal is None else int(event.ordinal)
        rows[idx]["event_type"] = int(event.event_type)
        rows[idx]["sip_timestamp_us"] = int(event.sip_timestamp_us)
        rows[idx]["price_primary_int"] = int(event.price_primary_int)
        rows[idx]["price_secondary_int"] = int(event.price_secondary_int)
        rows[idx]["size_primary"] = float(event.size_primary)
        rows[idx]["size_secondary"] = float(event.size_secondary)
        rows[idx]["exchange_primary"] = int(event.exchange_primary)
        rows[idx]["exchange_secondary"] = int(event.exchange_secondary)
        rows[idx]["event_flags"] = int(event.event_flags)
        rows[idx]["conditions_packed"] = int(event.conditions_packed)
    return rows


def maybe_polars_sort_rows(rows: np.ndarray) -> np.ndarray:
    """Sort event rows with Polars when available, otherwise NumPy.

    Polars is useful for bounded in-memory ticker/day blocks. The fallback keeps
    the package usable in lean training environments.
    """

    def numpy_sort() -> np.ndarray:
        order = np.lexsort((rows["event_type"], rows["ordinal"], rows["sip_timestamp_us"]))
        return rows[order].copy()

    try:
        import polars as pl
    except ModuleNotFoundError:
        return numpy_sort()

    try:
        data = {name: rows[name] for name in rows.dtype.names or ()}
        frame = pl.DataFrame(data).sort(["sip_timestamp_us", "ordinal", "event_type"])
        out = np.zeros((len(frame),), dtype=rows.dtype)
        for name in rows.dtype.names or ():
            out[name] = frame[name].to_numpy()
        return out
    except Exception:
        return numpy_sort()
