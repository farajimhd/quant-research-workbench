from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from research.mlops.data.audit import audit_temporal_batch
from research.mlops.data.config import DataProviderConfig, ExternalAsOfContextConfig, MarketStreamConfig, RollingMarketDataConfig, TickerBlockDataConfig
from research.mlops.data.providers import StreamingReplayBatchProvider
from research.mlops.data.replay import iter_replay_batches
from research.mlops.data.rolling import MacroBarFrame, RollingMarketSampleEngine, synthetic_rows_by_ticker
from research.mlops.data.sources import InMemoryEventSource
from research.mlops.data.ticker_blocks import TickerCursor, TickerEpochScheduler, build_event_time_bar_batch, build_requests, make_synthetic_event_rows
from research.mlops.data.contracts import CompactEvent


class FakeEncoderModel:
    def __init__(self, embedding_dim: int = 32) -> None:
        self.embedding_dim = int(embedding_dim)

    def encode(self, headers_uint8: np.ndarray, events_uint8: np.ndarray) -> np.ndarray:
        base = headers_uint8.astype(np.float32).mean(axis=1, keepdims=True) / 255.0
        event_mean = events_uint8.astype(np.float32).mean(axis=(1, 2), keepdims=False).reshape(-1, 1) / 255.0
        features = np.concatenate([base, event_mean], axis=1)
        tiled = np.tile(features, (1, (self.embedding_dim + features.shape[1] - 1) // features.shape[1]))
        return tiled[:, : self.embedding_dim].astype(np.float32, copy=False)


def make_synthetic_events(count: int = 1024, *, ticker: str = "TEST") -> tuple[CompactEvent, ...]:
    events: list[CompactEvent] = []
    for idx in range(int(count)):
        ask_cents = 10_000 + idx % 50
        bid_cents = ask_cents - 1
        events.append(
            CompactEvent(
                ticker=ticker,
                sip_timestamp_us=1_700_000_000_000_000 + idx * 1000,
                event_type=0,
                price_primary_int=ask_cents,
                price_secondary_int=bid_cents,
                size_primary=100.0 + (idx % 10),
                size_secondary=100.0,
                exchange_primary=1,
                exchange_secondary=1,
                event_flags=0x04,
                conditions_packed=0,
                ordinal=idx,
            )
        )
    return tuple(events)


def main() -> int:
    smoke_streaming_replay()
    smoke_ticker_blocks()
    smoke_rolling_provider()
    return 0


def smoke_streaming_replay() -> None:
    config = DataProviderConfig(
        provider_name="smoke_streaming_replay",
        market=MarketStreamConfig(
            chunk_stride_events=16,
            encoder_batch_size=8,
            temporal_batch_size=2,
            recent_context_embeddings=2,
            older_context_embeddings=0,
            future_chunks=1,
        ),
    )
    provider = StreamingReplayBatchProvider(
        config=config,
        event_source=InMemoryEventSource(make_synthetic_events(1024)),
        encoder_model=FakeEncoderModel(embedding_dim=config.market.embedding_dim),
    )
    batch = next(iter_replay_batches(provider, max_batches=1))
    audit = audit_temporal_batch(batch)
    assert audit.ok, audit
    assert batch.market_embeddings.shape == (2, 2, config.market.embedding_dim)
    assert "future_market_chunks_uint8" in batch.labels
    assert batch.profile is not None
    metrics = batch.profile.to_metrics()
    assert metrics["data/samples_created"] == 2.0
    print(
        "mlops_data_smoke_ok "
        f"market={batch.market_embeddings.shape} labels={batch.labels['future_market_chunks_uint8'].shape} "
        f"samples_per_sec={metrics['data/samples_per_second']:.1f}"
    )


def smoke_ticker_blocks() -> None:
    config = TickerBlockDataConfig(
        ticker_group_size=2,
        events_per_ticker_block=256,
        future_tail_events=512,
        sample_stride_events=32,
        max_samples_per_ticker=4,
    )
    cursors = [
        TickerCursor(ticker="AAA", first_ordinal=0, next_origin_ordinal=127, last_ordinal=4096, event_count=4096),
        TickerCursor(ticker="BBB", first_ordinal=0, next_origin_ordinal=127, last_ordinal=4096, event_count=4096),
    ]
    scheduler = TickerEpochScheduler.from_cursors(cursors, seed=123)
    selected = scheduler.select_next(2)
    assert {cursor.ticker for cursor in selected} == {"AAA", "BBB"}
    requests = build_requests(selected, config)
    rows_by_ticker = {request.ticker: make_synthetic_event_rows(request.expected_rows, request.low_ordinal) for request in requests}
    batch = build_event_time_bar_batch(rows_by_ticker, requests, config, provider_name="smoke_ticker_block")
    assert batch.header_uint8.shape[0] == 8
    assert batch.events_uint8.shape == (8, 128, 16)
    assert "future_bar_1s_high" in batch.labels
    assert batch.labels["future_bar_1s_high"].shape == (8,)
    assert batch.profile.samples_created == 8
    scheduler.update_after_success(requests)
    assert scheduler.cursors["AAA"].next_origin_ordinal > 127
    assert scheduler.cursors["BBB"].next_origin_ordinal > 127
    print(
        "ticker_block_smoke_ok "
        f"samples={batch.header_uint8.shape[0]} labels={len(batch.labels)} "
        f"samples_per_sec={batch.profile.samples_per_second():.1f}"
    )


def smoke_rolling_provider() -> None:
    config = RollingMarketDataConfig(
        short_context_chunks=4,
        long_context_lags=(8, 16),
        sample_stride_events=32,
        batch_size=8,
        max_ready_samples=16,
        external_contexts=(ExternalAsOfContextConfig(name="news", timestamp_column="timestamp_ns", timestamp_unit="ns", payload_columns=("headline",)),),
    )
    engine = RollingMarketSampleEngine(config)
    engine.append_rows_by_ticker(synthetic_rows_by_ticker(tickers=2, rows_per_ticker=1024))
    engine.load_macro_bars(
        MacroBarFrame(
            rows=[
                {"sym": "T0000", "timeframe": "1d", "bar_start_ms": 1_699_999_000_000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100, "dollar_volume": 150, "trade_count": 10, "quote_count": 50, "vwap": 1.4},
                {"sym": "T0000", "timeframe": "1d", "bar_start_ms": 1_700_000_100_000, "open": 2, "high": 3, "low": 1.5, "close": 2.5, "volume": 200, "dollar_volume": 500, "trade_count": 20, "quote_count": 60, "vwap": 2.2},
                {"sym": "SPY", "timeframe": "1d", "bar_start_ms": 1_699_999_000_000, "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1000, "dollar_volume": 10_500, "trade_count": 100, "quote_count": 500, "vwap": 10.3},
            ]
        )
    )
    engine.load_external_context("news", [{"ticker": "T0000", "timestamp_ns": 1_700_000_000_001_000_000, "id": "n1", "headline": "test"}])
    engine.load_external_context(
        "sec_filings",
        [
            {
                "ticker": "T0000",
                "timestamp_us": 1_700_000_000_001_000,
                "source_id": "sec1",
                "form_type": "10-Q",
                "company_name": "Test Company",
                "items": "Item 2",
                "texts": [{"text_kind": "body", "text": "Management discussion text"}],
            }
        ],
    )
    engine.load_external_context(
        "xbrl",
        [
            {
                "ticker": "T0000",
                "timestamp_us": 1_700_000_000_001_000,
                "source_id": "x1",
                "taxonomy": "us-gaap",
                "tag": "RevenueFromContractWithCustomerExcludingAssessedTax",
                "unit_code": "USD",
                "fiscal_year": 2026,
                "form_type": "10-Q",
                "period_end_date": "2026-03-31",
                "value": "1234567.89",
            }
        ],
    )
    samples = engine.build_ready_indices()
    assert len(samples) == 16
    assert len(samples[0].chunk_windows) == len(config.context_lags)
    assert samples[0].chunk_windows[0].end_ordinal - samples[0].chunk_windows[0].start_ordinal == 127
    batch = engine.materialize_training_batch(samples[:8])
    assert batch.headers_uint8.shape == (8, len(config.context_lags), 14)
    assert batch.events_uint8.shape == (8, len(config.context_lags), 128, 16)
    assert bool(batch.context_mask.all())
    assert "1d_close" in batch.macro_features
    assert "session_trade_count_so_far" in batch.macro_features
    assert "SPY_1d_close" in batch.global_features
    assert "future_1d_close" in batch.labels
    assert "future_intraday_bar_100ms_high" in batch.labels
    assert "news" in batch.external_context
    assert batch.text_inputs["news"]["input_ids"].shape == (8, config.news_max_items, config.text_max_tokens)
    assert batch.text_inputs["sec_filings"]["attention_mask"].shape == (8, config.sec_max_items, config.text_max_tokens)
    assert batch.xbrl_inputs["value"].shape == (8, config.xbrl_max_items)
    assert batch.xbrl_inputs["mask"].shape == (8, config.xbrl_max_items)
    lookup = {}
    for sample_index, ticker in enumerate(batch.ticker.tolist()):
        for origin in batch.chunk_origin_ordinal[sample_index].tolist():
            lookup[(ticker, int(origin))] = np.ones((32,), dtype=np.float32)
    prod = engine.materialize_production_batch(samples[:8], lookup)
    assert prod.market_embeddings.shape == (8, len(config.context_lags), 32)
    assert bool(prod.market_mask.all())
    engine.mark_processed(samples[:8])
    engine.trim_processed_tails()
    assert all(rows.shape[0] >= config.carryover_events for rows in engine.rows_by_ticker.values())
    print(
        "rolling_provider_smoke_ok "
        f"samples={len(samples)} context_chunks={len(config.context_lags)} "
        f"carryover={config.carryover_events}"
    )
    live_config = RollingMarketDataConfig(
        short_context_chunks=2,
        long_context_lags=(),
        sample_stride_events=64,
        batch_size=4,
        max_ready_samples=4,
    )
    live_engine = RollingMarketSampleEngine(live_config)
    live_engine.append_compact_events(make_synthetic_events(384, ticker="LIVE"))
    live_samples = live_engine.build_ready_indices()
    assert len(live_samples) == 4
    live_batch = live_engine.materialize_training_batch(live_samples)
    assert live_batch.headers_uint8.shape == (4, 2, 14)
    assert live_batch.events_uint8.shape == (4, 2, 128, 16)
    print(f"rolling_live_append_smoke_ok samples={len(live_samples)}")


if __name__ == "__main__":
    raise SystemExit(main())

