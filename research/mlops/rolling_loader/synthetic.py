from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

import numpy as np

from research.mlops.data.ticker_blocks import make_synthetic_event_rows
from research.mlops.rolling_loader.cache import ExternalContextPayload
from research.mlops.rolling_loader.config import RollingLoaderConfig, SyntheticRollingLoaderConfig


@dataclass(frozen=True, slots=True)
class SyntheticEvent:
    ticker: str
    row: np.void


@dataclass(frozen=True, slots=True)
class SyntheticContextUpdate:
    kind: str
    ticker: str
    timestamp_us: int
    payload: ExternalContextPayload
    global_item: bool = False


def synthetic_rows_by_ticker(config: SyntheticRollingLoaderConfig) -> dict[str, np.ndarray]:
    return {
        f"T{index:04d}": make_synthetic_event_rows(int(config.rows_per_ticker), low_ordinal=0)
        for index in range(int(config.tickers))
    }


def iter_synthetic_events(rows_by_ticker: dict[str, np.ndarray]) -> Iterator[SyntheticEvent]:
    """Merge synthetic ticker rows chronologically without building a huge table."""

    positions = {ticker: 0 for ticker in rows_by_ticker}
    remaining = True
    while remaining:
        remaining = False
        best_ticker = ""
        best_ts: int | None = None
        for ticker, rows in rows_by_ticker.items():
            pos = positions[ticker]
            if pos >= rows.shape[0]:
                continue
            remaining = True
            ts = int(rows[pos]["sip_timestamp_us"])
            if best_ts is None or ts < best_ts:
                best_ts = ts
                best_ticker = ticker
        if best_ts is None:
            break
        pos = positions[best_ticker]
        positions[best_ticker] = pos + 1
        yield SyntheticEvent(ticker=best_ticker, row=rows_by_ticker[best_ticker][pos])


def synthetic_external_updates(
    *,
    ticker: str,
    row: np.void,
    event_index: int,
    synthetic_config: SyntheticRollingLoaderConfig,
) -> list[SyntheticContextUpdate]:
    every = max(1, int(synthetic_config.external_every_events))
    if event_index % every != 0:
        return []
    loader_config = synthetic_config.loader
    ts = int(row["sip_timestamp_us"])
    rng_value = int(row["ordinal"]) % 251
    updates: list[SyntheticContextUpdate] = []
    updates.append(
        SyntheticContextUpdate(
            kind="ticker_news",
            ticker=ticker,
            timestamp_us=ts,
            payload=_token_payload("ticker_news", loader_config, rng_value, chunks=loader_config.news_token_chunks),
        )
    )
    updates.append(
        SyntheticContextUpdate(
            kind="global_news",
            ticker="GLOBAL",
            timestamp_us=ts,
            payload=_token_payload("global_news", loader_config, rng_value + 7, chunks=loader_config.news_token_chunks),
            global_item=True,
        )
    )
    updates.append(
        SyntheticContextUpdate(
            kind="sec_filing",
            ticker=ticker,
            timestamp_us=ts,
            payload=_token_payload("sec_filing", loader_config, rng_value + 13, chunks=loader_config.sec_token_chunks),
        )
    )
    updates.append(
        SyntheticContextUpdate(
            kind="xbrl",
            ticker=ticker,
            timestamp_us=ts,
            payload=_numeric_payload("xbrl", width=loader_config.xbrl_feature_width, offset=rng_value),
        )
    )
    updates.append(
        SyntheticContextUpdate(
            kind="ticker_macro_bar",
            ticker=ticker,
            timestamp_us=ts,
            payload=_numeric_payload("ticker_macro_bar", width=loader_config.bar_feature_width, offset=rng_value),
        )
    )
    updates.append(
        SyntheticContextUpdate(
            kind="global_market_bar",
            ticker="SPY",
            timestamp_us=ts,
            payload=_numeric_payload("global_market_bar", width=loader_config.bar_feature_width, offset=rng_value),
            global_item=False,
        )
    )
    return updates


def synthetic_external_updates_for_block(*, block: Any, synthetic_config: SyntheticRollingLoaderConfig) -> list[SyntheticContextUpdate]:
    """Build synthetic low-frequency updates from a fetched event block.

    This mirrors the production/training flow: low-frequency updates are
    selected for the full timestamp/ordinal block, then pushed into caches
    before sample indices are materialized from event origins in that block.
    """

    every = max(1, int(synthetic_config.external_every_events))
    rows = block.rows
    if rows.size == 0:
        return []
    update_positions = np.flatnonzero((rows["ordinal"].astype(np.int64, copy=False) % every) == 0)
    updates: list[SyntheticContextUpdate] = []
    for position in update_positions:
        ticker = block.tickers[int(block.ticker_index[int(position)])]
        updates.extend(
            synthetic_external_updates(
                ticker=ticker,
                row=rows[int(position)],
                event_index=int(rows[int(position)]["ordinal"]),
                synthetic_config=synthetic_config,
            )
        )
    return updates


def _token_payload(kind: str, config: RollingLoaderConfig, offset: int, *, chunks: int) -> ExternalContextPayload:
    token_count = int(config.text_max_tokens)
    token_ids = (np.arange(int(chunks) * token_count, dtype=np.uint32).reshape(int(chunks), token_count) + int(offset)) % 32000
    attention_mask = np.ones_like(token_ids, dtype=np.uint8)
    category_ids = np.asarray([offset % 97, offset % 31], dtype=np.uint16)
    time_features = np.asarray([offset / 251.0, (offset % 24) / 24.0], dtype=np.float32)
    return ExternalContextPayload(
        kind=kind,
        token_ids=token_ids.astype(np.uint32, copy=False),
        attention_mask=attention_mask,
        category_ids=category_ids,
        time_features=time_features,
    )


def _numeric_payload(kind: str, *, width: int, offset: int) -> ExternalContextPayload:
    values = np.linspace(0.0, 1.0, int(width), dtype=np.float32) + np.float32(offset / 251.0)
    time_features = np.asarray([offset / 251.0, (offset % 7) / 7.0], dtype=np.float32)
    return ExternalContextPayload(kind=kind, numeric_values=values, time_features=time_features)
