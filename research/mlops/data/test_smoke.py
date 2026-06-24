from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from research.mlops.data.audit import audit_temporal_batch
from research.mlops.data.config import DataProviderConfig, MarketStreamConfig, TickerBlockDataConfig
from research.mlops.data.providers import StreamingReplayBatchProvider
from research.mlops.data.replay import iter_replay_batches
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


if __name__ == "__main__":
    raise SystemExit(main())

