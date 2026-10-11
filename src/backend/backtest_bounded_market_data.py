"""Complete bounded certified reads without changing feature producer identity."""
from contextlib import closing
import json
from typing import Any, Iterator, Mapping
from .backtest_market_data import (
    CertifiedMarketDayPlan, iter_market_day_rows, market_day_source_sqls,
    readonly_clickhouse_client,
)


class MarketWindowCapacityExceeded(ValueError):
    """An unexposed market window exceeded its declared buffer capacity."""

    def __init__(self, rows: int, serialized_bytes: int):
        self.rows = rows
        self.serialized_bytes = serialized_bytes
        super().__init__(
            f"Bounded market window exceeds declared capacity: "
            f"rows={rows}, serialized_bytes={serialized_bytes}")


def iter_bounded_market_day_rows(
    plan: CertifiedMarketDayPlan, client=None, *, through_boundary_ms: int,
    after_boundary_ms: int = 0, window_ms: int = 60_000,
    max_window_rows: int = 250_000, max_window_bytes: int = 128_000_000,
    subdivide_oversized_windows: bool = False,
    price_plan: Any | None = None,
    candidate_boundaries: Mapping[str, tuple[int, ...]] | None = None,
) -> Iterator[dict[str, Any]]:
    """Finish bounded pinned reads before exposing rows to a slow consumer.

    A failed window yields no rows. No partial stream is retried. Limits fail
    closed rather than truncating data; the caller must qualify its capacity.
    """
    for name, value in (("window_ms", window_ms),
                        ("max_window_rows", max_window_rows),
                        ("max_window_bytes", max_window_bytes)):
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if type(subdivide_oversized_windows) is not bool:
        raise ValueError("Window subdivision selection must be boolean")
    if window_ms % 100:
        raise ValueError("Market read window must align to 100ms")
    # Validate the full range and candidate contract before opening a socket.
    market_day_source_sqls(plan, through_boundary_ms=through_boundary_ms,
                          after_boundary_ms=after_boundary_ms,
                          price_plan=price_plan,
                          candidate_boundaries=candidate_boundaries)
    active = client or readonly_clickhouse_client(market_stream=True, v3_read_principal=True)
    owned = client is None
    lower = after_boundary_ms
    previous = None
    try:
        while lower < through_boundary_ms:
            upper = min(lower + window_ms, through_boundary_ms)
            candidates = (None if candidate_boundaries is None else {
                ticker: tuple(clock for clock in clocks if lower < clock <= upper)
                for ticker, clocks in candidate_boundaries.items()
                if any(lower < clock <= upper for clock in clocks)
            })
            rows = []
            wire_bytes = 0
            window_previous = previous
            capacity_error = None
            if candidates is None or candidates:
                with closing(iter_market_day_rows(
                    plan, client=active, after_boundary_ms=lower,
                    through_boundary_ms=upper, price_plan=price_plan,
                    candidate_boundaries=candidates,
                )) as source:
                    for row in source:
                        key = (str(row["session_date"]), int(row["boundary_ms"]),
                               str(row["ticker"]), int(row["resolution_ms"]))
                        if not lower < key[1] <= upper:
                            raise ValueError("Market row falls outside its bounded window")
                        if previous is not None and key <= previous:
                            raise ValueError("Bounded market rows are duplicated or unordered")
                        previous = key
                        wire_bytes += len(json.dumps(row, separators=(",", ":")).encode("utf-8"))
                        if len(rows) >= max_window_rows or wire_bytes > max_window_bytes:
                            capacity_error = MarketWindowCapacityExceeded(len(rows) + 1, wire_bytes)
                            break
                        rows.append(row)
            if capacity_error is not None:
                if not subdivide_oversized_windows or upper - lower <= 100:
                    raise capacity_error
                # Discard the unexposed buffer and close its response before
                # rereading disjoint smaller windows with identical source pins.
                rows.clear()
                previous = window_previous
                smaller = max(100, ((upper - lower) // 200) * 100)
                with closing(iter_bounded_market_day_rows(
                    plan, client=active, after_boundary_ms=lower,
                    through_boundary_ms=upper, window_ms=smaller,
                    max_window_rows=max_window_rows, max_window_bytes=max_window_bytes,
                    subdivide_oversized_windows=True, price_plan=price_plan,
                    candidate_boundaries=candidates,
                )) as source:
                    for row in source:
                        previous = (str(row["session_date"]), int(row["boundary_ms"]),
                                    str(row["ticker"]), int(row["resolution_ms"]))
                        yield row
            else:
                yield from rows
            lower = upper
    finally:
        if owned:
            active.close()


