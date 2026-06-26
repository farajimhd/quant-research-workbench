from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

if __package__ in {None, ""}:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "research").is_dir():
            sys.path.insert(0, str(parent))
            break

from research.mlops.rolling_loader.config import RollingLoaderConfig, SyntheticRollingLoaderConfig
from research.mlops.data.rolling import RollingReadyIndexBlock
from research.mlops.rolling_loader.loader import RollingContextLoader
from research.mlops.rolling_loader.materialized_cache import partition_ready_blocks
from research.mlops.rolling_loader.profiler import RollingLoaderProfiler
from research.mlops.rolling_loader.run_build_materialized_cache import _default_ready_sample_cap, _build_task_specs
from research.mlops.rolling_loader.synthetic import iter_synthetic_events, synthetic_external_updates, synthetic_rows_by_ticker


def main() -> int:
    assert _default_ready_sample_cap(workers=64, builder_batch_size=4096, sample_multiple=4096) == 262144
    assert _default_ready_sample_cap(workers=3, builder_batch_size=1000, sample_multiple=4096) == 4096

    rows = np.zeros((1000,), dtype=[("ordinal", "<i8")])
    large_block = RollingReadyIndexBlock(ticker="BIG", rows=rows, origin_offsets=np.arange(1000, dtype=np.int64))
    partitions = partition_ready_blocks([large_block], workers=4)
    partition_counts = [sum(block.sample_count for block in partition) for partition in partitions]
    assert len(partitions) == 4
    assert all(count > 0 for count in partition_counts), partition_counts
    assert max(partition_counts) - min(partition_counts) <= 1, partition_counts
    task_specs = _build_task_specs((large_block,), batch_size=250, workers=4)
    assert [worker_id for worker_id, _blocks in task_specs[:4]] == [0, 1, 2, 3]
    assert [int(blocks[0].origin_offsets[0]) for _worker_id, blocks in task_specs[:4]] == [0, 250, 500, 750]

    loader_config = RollingLoaderConfig(
        batch_size=16,
        context_chunk_stride_events=4,
        short_context_chunks=4,
        long_context_lags=(8, 16),
        sample_stride_events=1,
    )
    synthetic_config = SyntheticRollingLoaderConfig(tickers=4, rows_per_ticker=512, external_every_events=32, loader=loader_config)
    profiler = RollingLoaderProfiler(enabled=True)
    loader = RollingContextLoader(loader_config, profiler=profiler)
    rows_by_ticker = synthetic_rows_by_ticker(synthetic_config)
    initialized = loader.initialize_universe(rows_by_ticker)
    assert len(initialized) == synthetic_config.tickers
    initialized_summary = loader.cache_summary()
    assert initialized_summary["initialized_tickers"] == synthetic_config.tickers
    assert initialized_summary["event_tickers"] == synthetic_config.tickers
    assert initialized_summary["ticker_news_rings"] == synthetic_config.tickers
    assert initialized_summary["sec_filing_rings"] == synthetic_config.tickers
    assert initialized_summary["xbrl_rings"] == synthetic_config.tickers
    assert initialized_summary["ticker_macro_bar_rings"] == synthetic_config.tickers
    warm_count = loader_config.warmup_events_per_ticker
    loader.warm_load_events({ticker: rows[:warm_count] for ticker, rows in rows_by_ticker.items()})
    replay_rows = {ticker: rows[warm_count:] for ticker, rows in rows_by_ticker.items()}
    for index, event in enumerate(iter_synthetic_events(replay_rows), start=1):
        for update in synthetic_external_updates(ticker=event.ticker, row=event.row, event_index=index, synthetic_config=synthetic_config):
            loader.push_external(
                kind=update.kind,
                ticker=update.ticker,
                timestamp_us=update.timestamp_us,
                payload=update.payload,
                global_item=update.global_item,
            )
        loader.push_event(event.ticker, event.row)
        if len(loader.ready_samples) >= loader_config.batch_size:
            break
    assert len(loader.ready_samples) >= loader_config.batch_size, "expected ready samples after synthetic replay"
    samples = loader.drain_ready_samples(loader_config.batch_size)
    batch = loader.materialize_training_batch(samples, materialize_external_payloads=True)
    assert batch.headers_uint8.shape == (loader_config.batch_size, loader_config.context_chunks, loader_config.header_bytes)
    assert batch.events_uint8.shape == (
        loader_config.batch_size,
        loader_config.context_chunks,
        loader_config.events_per_chunk,
        loader_config.event_bytes,
    )
    assert batch.nbytes > 0
    assert loader.cache_summary()["chunk_arena_items"] > 0
    print("SMOKE OK")
    print(profiler.snapshot()["counters"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
