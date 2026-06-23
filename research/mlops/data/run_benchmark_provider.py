from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from research.mlops.data.config import DataProviderConfig, MarketStreamConfig
from research.mlops.data.providers import StreamingReplayBatchProvider
from research.mlops.data.replay import iter_replay_batches
from research.mlops.data.sources import InMemoryEventSource
from research.mlops.data.test_smoke import FakeEncoderModel, make_synthetic_events


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark mlops data providers with synthetic market events.")
    parser.add_argument("--batches", type=int, default=4)
    parser.add_argument("--events", type=int, default=2048)
    parser.add_argument("--encoder-batch-size", type=int, default=256)
    parser.add_argument("--temporal-batch-size", type=int, default=32)
    parser.add_argument("--chunk-stride-events", type=int, default=8)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = DataProviderConfig(
        provider_name="synthetic_streaming_replay",
        market=MarketStreamConfig(
            chunk_stride_events=int(args.chunk_stride_events),
            encoder_batch_size=int(args.encoder_batch_size),
            temporal_batch_size=int(args.temporal_batch_size),
            recent_context_embeddings=4,
            older_context_embeddings=0,
            future_chunks=1,
        ),
    )
    provider = StreamingReplayBatchProvider(
        config=config,
        event_source=InMemoryEventSource(make_synthetic_events(int(args.events))),
        encoder_model=FakeEncoderModel(embedding_dim=config.market.embedding_dim),
    )
    profiles = []
    for idx, batch in enumerate(iter_replay_batches(provider, max_batches=int(args.batches)), start=1):
        profile = batch.profile
        if profile is not None:
            profiles.append(profile)
            metrics = profile.to_metrics()
            print(
                f"batch={idx} samples={len(batch.samples)} "
                f"total={metrics['data/total_seconds']:.4f}s "
                f"samples_per_sec={metrics['data/samples_per_second']:.1f}"
            )
    if profiles:
        totals = np.asarray([profile.total_seconds for profile in profiles], dtype=np.float64)
        print(f"p50_total_seconds={np.quantile(totals, 0.50):.4f}")
        print(f"p95_total_seconds={np.quantile(totals, 0.95):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

