from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from research.mlops.compact_events import QUOTE_EVENT_TYPE, TRADE_EVENT_TYPE
from research.mlops.data.audit import audit_temporal_batch
from research.mlops.data.config import DataProviderConfig, ExternalAsOfContextConfig, MarketStreamConfig, RollingMarketDataConfig, TickerBlockDataConfig
from research.mlops.data.providers import StreamingReplayBatchProvider
from research.mlops.data.replay import iter_replay_batches
from research.mlops.data.rolling import MacroBarFrame, RollingMarketSampleEngine, synthetic_rows_by_ticker
from research.mlops.data.sources import InMemoryEventSource
from research.mlops.data.ticker_blocks import TickerCursor, TickerEpochScheduler, build_event_time_bar_batch, build_requests, make_synthetic_event_rows
from research.mlops.data.contracts import BAR_FEATURE_KEYS, CompactEvent, FUTURE_BAR_FEATURE_KEYS


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
    smoke_rolling_ready_index_balanced_cap()
    smoke_rolling_ordinal_gap_materialization()
    smoke_rolling_ready_index_filters_unencodable_windows()
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


def smoke_rolling_ready_index_balanced_cap() -> None:
    config = RollingMarketDataConfig(
        short_context_chunks=2,
        long_context_lags=(),
        sample_stride_events=1,
        batch_size=8,
        max_ready_samples=24,
    )
    engine = RollingMarketSampleEngine(config)
    engine.append_rows_by_ticker(synthetic_rows_by_ticker(tickers=6, rows_per_ticker=512))
    blocks = engine.build_ready_index_blocks(max_samples=24)
    counts = {block.ticker: block.sample_count for block in blocks}
    assert len(blocks) == 6, counts
    assert sum(counts.values()) == 24, counts
    assert set(counts.values()) == {4}, counts
    for block in blocks:
        assert np.all(block.origin_offsets[1:] == block.origin_offsets[:-1] + 1)
        rows = engine.rows_by_ticker[block.ticker]
        ordinals = rows["ordinal"][block.origin_offsets]
        assert np.all(ordinals[1:] == ordinals[:-1] + 1)
    engine.mark_processed(engine.build_ready_indices(max_samples=24))
    next_blocks = engine.build_ready_index_blocks(max_samples=24)
    next_counts = {block.ticker: block.sample_count for block in next_blocks}
    assert len(next_blocks) == 6, next_counts
    for previous, current in zip(blocks, next_blocks, strict=True):
        assert previous.ticker == current.ticker
        assert int(current.origin_offsets[0]) == int(previous.origin_offsets[-1]) + 1
    print("rolling_ready_index_balanced_cap_ok tickers=6 samples=24")


def smoke_rolling_ordinal_gap_materialization() -> None:
    config = RollingMarketDataConfig(
        short_context_chunks=1,
        long_context_lags=(),
        sample_stride_events=1,
        batch_size=2,
        max_ready_samples=8,
        q_live_contexts=(),
    )
    engine = RollingMarketSampleEngine(config)
    rows = make_synthetic_event_rows(512, low_ordinal=0)
    rows = rows[rows["ordinal"] != 10]
    engine.append_rows_by_ticker({"GAP": rows})

    samples = engine.build_ready_indices(max_samples=8)
    assert samples
    sample = samples[0]
    window = sample.chunk_windows[0]
    assert int(window.start_ordinal) > 10
    assert int(window.end_ordinal) - int(window.start_ordinal) == config.events_per_chunk - 1

    batch = engine.materialize_training_batch(samples[:2])
    assert batch.headers_uint8.shape == (2, 1, 14)
    assert batch.events_uint8.shape == (2, 1, 128, 16)
    engine.mark_processed(samples[:2])
    expected_processed = int(np.searchsorted(rows["ordinal"], int(samples[1].origin_ordinal), side="left")) + 1
    assert engine._processed_offsets["GAP"] == expected_processed
    print("rolling_ordinal_gap_materialization_ok samples=2")


def smoke_rolling_ready_index_filters_unencodable_windows() -> None:
    config = RollingMarketDataConfig(
        short_context_chunks=1,
        long_context_lags=(),
        sample_stride_events=1,
        batch_size=2,
        max_ready_samples=4,
        q_live_contexts=(),
    )
    engine = RollingMarketSampleEngine(config)
    rows = make_synthetic_event_rows(384, low_ordinal=0)
    rows[: config.events_per_chunk]["event_type"] = TRADE_EVENT_TYPE
    rows[config.events_per_chunk]["event_type"] = QUOTE_EVENT_TYPE
    engine.append_rows_by_ticker({"ENC": rows})

    samples = engine.build_ready_indices(max_samples=4)
    assert samples
    assert int(samples[0].origin_ordinal) > config.events_per_chunk - 1
    progress_events: list[tuple[str, int, int]] = []
    batch = engine.materialize_training_batch(samples[:2], progress_callback=lambda stage, done, total: progress_events.append((stage, done, total)))
    assert batch.headers_uint8.shape == (2, 1, 14)
    assert batch.events_uint8.shape == (2, 1, 128, 16)
    assert any(stage == "encode" and done == total for stage, done, total in progress_events)
    assert any(stage == "text" and done == total for stage, done, total in progress_events)
    print("rolling_ready_index_filters_unencodable_windows_ok samples=2")


def smoke_rolling_provider() -> None:
    config = RollingMarketDataConfig(
        short_context_chunks=4,
        long_context_lags=(8, 16),
        sample_stride_events=32,
        batch_size=8,
        max_ready_samples=16,
        external_contexts=(ExternalAsOfContextConfig(name="ticker_news", timestamp_column="timestamp_ns", timestamp_unit="ns", payload_columns=("headline",)),),
    )
    engine = RollingMarketSampleEngine(config)
    engine.append_rows_by_ticker(synthetic_rows_by_ticker(tickers=2, rows_per_ticker=1024))
    engine.load_macro_bars(
        MacroBarFrame(
            rows=[
                {"sym": "T0000", "timeframe": "1d", "bar_start_ms": 1_699_833_600_000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100, "dollar_volume": 150, "trade_count": 10, "quote_count": 50, "vwap": 1.4},
                {"sym": "T0000", "timeframe": "1d", "bar_start_ms": 1_699_920_000_000, "open": 2, "high": 3, "low": 1.5, "close": 2.5, "volume": 200, "dollar_volume": 500, "trade_count": 20, "quote_count": 60, "vwap": 2.2},
                {"sym": "T0000", "timeframe": "1d", "bar_start_ms": 1_700_006_400_000, "open": 3, "high": 4, "low": 2.5, "close": 3.5, "volume": 300, "dollar_volume": 1050, "trade_count": 30, "quote_count": 70, "vwap": 3.2},
                {"sym": "T0000", "timeframe": "1d", "bar_start_ms": 1_700_092_800_000, "open": 4, "high": 5, "low": 3.5, "close": 4.5, "volume": 400, "dollar_volume": 1800, "trade_count": 40, "quote_count": 80, "vwap": 4.2},
                {"sym": "SPY", "timeframe": "1d", "bar_start_ms": 1_699_833_600_000, "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1000, "dollar_volume": 10_500, "trade_count": 100, "quote_count": 500, "vwap": 10.3},
                {"sym": "SPY", "timeframe": "1d", "bar_start_ms": 1_699_920_000_000, "open": 11, "high": 12, "low": 10, "close": 11.5, "volume": 1100, "dollar_volume": 12_650, "trade_count": 110, "quote_count": 510, "vwap": 11.3},
                {"sym": "SPY", "timeframe": "1d", "bar_start_ms": 1_700_006_400_000, "open": 12, "high": 13, "low": 11, "close": 12.5, "volume": 1200, "dollar_volume": 15_000, "trade_count": 120, "quote_count": 520, "vwap": 12.3},
            ]
        )
    )
    engine.load_external_context(
        "ticker_news",
        [
            {
                "ticker": "T0000",
                "timestamp_ns": 1_700_000_000_001_000_000,
                "id": "n1",
                "headline": "test",
                "provider": "benzinga",
                "url_domain": "benzinga.com",
                "channels": "analyst-ratings,earnings",
                "provider_tags": "AAPL,earnings",
                "quality_flags": "ok",
            }
        ],
    )
    engine.load_external_context(
        "market_news",
        [
            {
                "ticker": "__MARKET__",
                "timestamp_us": 1_700_000_000_001_000,
                "source_id": "mn1",
                "title": "Market-wide test news",
                "provider": "benzinga",
                "url_domain": "benzinga.com",
                "channels": "macro",
                "provider_tags": "market",
                "quality_flags": "ok",
                "input_ids": [101, 102],
                "attention_mask": [1, 1],
                "token_chunk_index": 0,
            }
        ],
    )
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
                "text_kind": "body",
                "quality_flags": "parsed",
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
                "xbrl_row_kind": "company_fact",
                "calendar_period_code": "CY2026Q1",
                "location_code": "",
                "accepted_at_source": "submissions_bulk_recent",
                "mapping_confidence_score": 0.95,
                "period_end_date": "2026-03-31",
                "value": "1234567.89",
            }
        ],
    )
    engine.load_category_references(
        [
            {"domain": "xbrl", "field_name": "taxonomy", "category_value": "us-gaap", "category_id": 1},
            {"domain": "xbrl", "field_name": "tag", "category_value": "RevenueFromContractWithCustomerExcludingAssessedTax", "category_id": 2},
            {"domain": "xbrl", "field_name": "unit_code", "category_value": "USD", "category_id": 3},
            {"domain": "xbrl", "field_name": "form_type", "category_value": "10-Q", "category_id": 4},
            {"domain": "xbrl", "field_name": "xbrl_row_kind", "category_value": "company_fact", "category_id": 5},
            {"domain": "news", "field_name": "provider", "category_value": "benzinga", "category_id": 11},
            {"domain": "news", "field_name": "url_domain", "category_value": "benzinga.com", "category_id": 12},
            {"domain": "news", "field_name": "channels", "category_value": "analyst-ratings", "category_id": 13},
            {"domain": "news", "field_name": "channels", "category_value": "earnings", "category_id": 14},
            {"domain": "news", "field_name": "channels", "category_value": "macro", "category_id": 15},
            {"domain": "news", "field_name": "provider_tags", "category_value": "AAPL", "category_id": 16},
            {"domain": "news", "field_name": "provider_tags", "category_value": "earnings", "category_id": 17},
            {"domain": "news", "field_name": "provider_tags", "category_value": "market", "category_id": 18},
            {"domain": "news", "field_name": "quality_flags", "category_value": "ok", "category_id": 19},
            {"domain": "sec_filings", "field_name": "form_type", "category_value": "10-Q", "category_id": 21},
            {"domain": "sec_filings", "field_name": "text_kind", "category_value": "body", "category_id": 22},
            {"domain": "sec_filings", "field_name": "quality_flags", "category_value": "parsed", "category_id": 23},
        ]
    )
    samples = engine.build_ready_indices()
    assert len(samples) == 16
    assert len(samples[0].chunk_windows) == len(config.context_lags)
    assert samples[0].chunk_windows[0].end_ordinal - samples[0].chunk_windows[0].start_ordinal == 127
    batch = engine.materialize_training_batch(samples[:8])
    assert batch.headers_uint8.shape == (8, len(config.context_lags), 14)
    assert batch.events_uint8.shape == (8, len(config.context_lags), 128, 16)
    assert batch.bar_feature_keys == BAR_FEATURE_KEYS
    assert batch.future_bar_feature_keys == FUTURE_BAR_FEATURE_KEYS
    assert batch.macro_bar_timeframes == ("today_asof", "past_1d", "past_2d", "past_3d", "past_7d", "past_14d", "past_28d", "past_40d", "past_200d")
    assert batch.global_bar_timeframes == ("today_asof", "past_1d", "past_2d", "past_7d")
    assert batch.future_macro_bar_timeframes == ("current_day_full", "plus_1d", "plus_2d", "plus_3d", "plus_7d", "plus_28d")
    assert batch.ticker_macro_bars.shape == (8, len(batch.macro_bar_timeframes), len(BAR_FEATURE_KEYS))
    assert batch.ticker_macro_bar_mask.shape == (8, len(batch.macro_bar_timeframes))
    assert batch.global_market_bars.shape == (8, len(config.global_symbols), len(batch.global_bar_timeframes), len(BAR_FEATURE_KEYS))
    assert batch.global_market_bar_mask.shape == (8, len(config.global_symbols), len(batch.global_bar_timeframes))
    assert batch.future_macro_bars.shape == (8, len(batch.future_macro_bar_timeframes), len(FUTURE_BAR_FEATURE_KEYS))
    assert batch.future_macro_bar_mask.shape == (8, len(batch.future_macro_bar_timeframes))
    assert batch.future_intraday_bars.shape == (8, len(config.intraday_label_horizons), len(FUTURE_BAR_FEATURE_KEYS))
    assert batch.future_intraday_bar_mask.shape == (8, len(config.intraday_label_horizons))
    assert float(batch.ticker_macro_bars[0, 0, BAR_FEATURE_KEYS.index("close")]) > 0.0
    assert float(batch.ticker_macro_bars[0, batch.macro_bar_timeframes.index("past_1d"), BAR_FEATURE_KEYS.index("close")]) == 1.5
    assert bool(batch.ticker_macro_bar_mask[0, batch.macro_bar_timeframes.index("past_200d")])
    assert float(batch.ticker_macro_bars[0, batch.macro_bar_timeframes.index("past_200d"), BAR_FEATURE_KEYS.index("close")]) == 1.5
    assert float(batch.future_macro_bars[0, batch.future_macro_bar_timeframes.index("current_day_full"), FUTURE_BAR_FEATURE_KEYS.index("close")]) == 2.5
    assert float(batch.future_macro_bars[0, batch.future_macro_bar_timeframes.index("plus_1d"), FUTURE_BAR_FEATURE_KEYS.index("close")]) == 3.5
    assert "today_asof_close" in batch.macro_features
    assert "past_1d_close" in batch.macro_features
    assert "1w_close" not in batch.macro_features
    assert "1mo_close" not in batch.macro_features
    assert "1y_close" not in batch.macro_features
    assert "session_trade_count_so_far" in batch.macro_features
    assert "SPY_today_asof_close" in batch.global_features
    assert "SPY_past_7d_close" in batch.global_features
    assert "future_current_day_full_close" in batch.labels
    assert "future_plus_1d_close" in batch.labels
    assert "future_1w_close" not in batch.labels
    assert "future_intraday_bar_100ms_high" in batch.labels
    assert "future_intraday_bar_100ms_vwap" not in batch.labels
    assert "ticker_news" in batch.external_context
    assert batch.text_inputs["ticker_news"]["input_ids"].shape == (8, config.news_max_items, config.news_token_chunks, config.text_max_tokens)
    assert batch.text_inputs["ticker_news"]["chunk_mask"].shape == (8, config.news_max_items, config.news_token_chunks)
    assert batch.text_inputs["ticker_news"]["time_delta_seconds"].shape == (8, config.news_max_items)
    assert batch.text_inputs["ticker_news"]["time_age_seconds_log1p"].shape == (8, config.news_max_items)
    assert "timestamp_us" not in batch.text_inputs["ticker_news"]
    assert batch.text_inputs["ticker_news"]["provider_id"][0, 0] == 11
    assert batch.text_inputs["ticker_news"]["url_domain_id"][0, 0] == 12
    assert batch.text_inputs["ticker_news"]["channels_ids"][0, 0, 0] == 13
    assert batch.text_inputs["ticker_news"]["channels_ids"][0, 0, 1] == 14
    assert bool(batch.text_inputs["ticker_news"]["channels_mask"][0, 0, 1])
    assert batch.text_inputs["ticker_news"]["provider_tags_ids"][0, 0, 0] == 16
    assert batch.text_inputs["ticker_news"]["quality_flags_ids"][0, 0, 0] == 19
    assert batch.text_inputs["market_news"]["input_ids"].shape == (8, config.market_news_max_items, config.market_news_token_chunks, config.text_max_tokens)
    assert batch.text_inputs["market_news"]["chunk_mask"].shape == (8, config.market_news_max_items, config.market_news_token_chunks)
    assert batch.text_inputs["market_news"]["provider_tags_ids"][0, 0, 0] == 18
    assert batch.text_inputs["sec_filings"]["attention_mask"].shape == (8, config.sec_max_items, config.sec_token_chunks, config.text_max_tokens)
    assert batch.text_inputs["sec_filings"]["chunk_mask"].shape == (8, config.sec_max_items, config.sec_token_chunks)
    assert batch.text_inputs["sec_filings"]["time_delta_seconds"].shape == (8, config.sec_max_items)
    assert batch.text_inputs["sec_filings"]["form_id"][0, 0] == 21
    assert batch.text_inputs["sec_filings"]["text_kind_id"][0, 0] == 22
    assert batch.text_inputs["sec_filings"]["quality_flags_ids"][0, 0, 0] == 23
    assert batch.xbrl_inputs["value"].shape == (8, config.xbrl_max_items)
    assert batch.xbrl_inputs["mask"].shape == (8, config.xbrl_max_items)
    assert batch.xbrl_inputs["time_delta_seconds"].shape == (8, config.xbrl_max_items)
    assert batch.xbrl_inputs["time_age_seconds_log1p"].shape == (8, config.xbrl_max_items)
    assert "timestamp_us" not in batch.xbrl_inputs
    assert batch.xbrl_inputs["row_kind_id"].shape == (8, config.xbrl_max_items)
    assert batch.xbrl_inputs["taxonomy_id"][0, 0] == 1
    assert batch.xbrl_inputs["tag_id"][0, 0] == 2
    assert batch.xbrl_inputs["unit_id"][0, 0] == 3
    assert batch.xbrl_inputs["form_id"][0, 0] == 4
    assert batch.xbrl_inputs["row_kind_id"][0, 0] == 5
    assert "accepted_at_source_id" not in batch.xbrl_inputs
    assert "calendar_period_id" not in batch.xbrl_inputs
    assert batch.xbrl_inputs["mapping_confidence"].shape == (8, config.xbrl_max_items)
    assert batch.chunk_time_features["time_delta_seconds"].shape == (8, len(config.context_lags))
    assert batch.chunk_time_features["time_age_seconds_log1p"].shape == (8, len(config.context_lags))
    assert batch.time_features["time_utc_second_of_day_sin"].shape == (8,)
    lookup = {}
    for sample in samples[:8]:
        for window in sample.chunk_windows:
            lookup[(sample.ticker, int(window.origin_ordinal))] = np.ones((32,), dtype=np.float32)
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

