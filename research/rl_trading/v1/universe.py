"""Deterministic causal top-volume market membership with mandatory holdings."""
from __future__ import annotations

import math
from typing import Iterable, Mapping


def volume_order(rows: Iterable[Mapping]) -> tuple[str, ...]:
    values: dict[str, float] = {}
    for row in rows:
        ticker = str(row['ticker'])
        volume = row.get('volume_60s')
        if ticker in values or not isinstance(volume, (int, float)) or not math.isfinite(volume) or volume < 0:
            raise ValueError('Top-N selection requires unique tickers and finite completed 60s volume')
        values[ticker] = float(volume)
    if not values:
        raise ValueError('Top-N market population is empty')
    return tuple(sorted(values, key=lambda ticker: (-values[ticker], ticker)))


def slots(order: tuple[str, ...], held: Iterable[str], top_n: int) -> tuple[str, ...]:
    if type(top_n) is not int or top_n < 1:
        raise ValueError('top_n must be positive')
    held_set = set(held)
    if not held_set <= set(order) or len(held_set) > top_n:
        raise ValueError('Held positions must fit in the certified market slots')
    # Held slots are stable by ticker, then the remaining slots follow current rank.
    result = sorted(held_set)
    result.extend(ticker for ticker in order if ticker not in held_set and len(result) < top_n)
    return tuple(result)
