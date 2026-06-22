from __future__ import annotations

import argparse
import asyncio

from market_ai.config import MarketAIConfig
from market_ai.runtime import MarketAIService, MarketAIServiceConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Market AI event batching and model-serving runtime.")
    parser.add_argument("--source", choices=("qmd", "synthetic"), default="qmd", help="Event source.")
    parser.add_argument("--qmd-url", default="ws://127.0.0.1:8795/stream/compact-events", help="qmd-gateway compact-event websocket URL.")
    parser.add_argument("--max-events", type=int, default=0, help="Stop after this many events. Zero means run until interrupted.")
    parser.add_argument("--events-per-chunk", type=int, default=128)
    parser.add_argument("--chunk-stride-events", type=int, default=1)
    parser.add_argument("--encoder-batch-size", type=int, default=8192)
    parser.add_argument("--temporal-batch-size", type=int, default=4096)
    parser.add_argument("--embedding-dim", type=int, default=32)
    parser.add_argument("--embedding-history", type=int, default=4096)
    parser.add_argument("--recent-context-embeddings", type=int, default=16)
    parser.add_argument("--recent-context-stride", type=int, default=1)
    parser.add_argument("--older-context-embeddings", type=int, default=48)
    parser.add_argument("--older-context-min-lag", type=int, default=32)
    parser.add_argument("--older-context-max-lag", type=int, default=2048)
    parser.add_argument("--terminal-refresh-seconds", type=float, default=0.5)
    parser.add_argument("--reconnect-delay-seconds", type=float, default=2.0, help="Delay before reconnecting to qmd websocket after an error.")
    parser.add_argument("--no-rich", action="store_true", help="Use plain periodic logs instead of Rich terminal.")
    parser.add_argument("--no-screen", action="store_true", help="Disable Rich alternate-screen mode.")
    parser.add_argument("--synthetic-tickers", default="AAPL,NVDA,TSLA,MSFT", help="Comma-separated tickers for synthetic source.")
    parser.add_argument("--synthetic-events-per-second", type=float, default=5000.0)
    return parser.parse_args()


def config_from_args(args: argparse.Namespace) -> tuple[MarketAIConfig, MarketAIServiceConfig]:
    market_config = MarketAIConfig(
        events_per_chunk=max(2, int(args.events_per_chunk)),
        chunk_stride_events=max(1, int(args.chunk_stride_events)),
        encoder_batch_size=max(1, int(args.encoder_batch_size)),
        temporal_batch_size=max(1, int(args.temporal_batch_size)),
        embedding_dim=max(1, int(args.embedding_dim)),
        embedding_history=max(1, int(args.embedding_history)),
        recent_context_embeddings=max(0, int(args.recent_context_embeddings)),
        recent_context_stride=max(1, int(args.recent_context_stride)),
        older_context_embeddings=max(0, int(args.older_context_embeddings)),
        older_context_min_lag=max(1, int(args.older_context_min_lag)),
        older_context_max_lag=max(1, int(args.older_context_max_lag)),
    )
    service_config = MarketAIServiceConfig(
        source=str(args.source),
        qmd_url=str(args.qmd_url),
        max_events=max(0, int(args.max_events)),
        terminal_refresh_seconds=max(0.1, float(args.terminal_refresh_seconds)),
        reconnect_delay_seconds=max(0.1, float(args.reconnect_delay_seconds)),
        rich_enabled=not bool(args.no_rich),
        rich_screen=not bool(args.no_screen),
        synthetic_tickers=tuple(ticker.strip().upper() for ticker in str(args.synthetic_tickers).split(",") if ticker.strip()),
        synthetic_events_per_second=max(1.0, float(args.synthetic_events_per_second)),
    )
    return market_config, service_config


def main() -> int:
    args = parse_args()
    market_config, service_config = config_from_args(args)
    service = MarketAIService(market_config=market_config, service_config=service_config)
    try:
        asyncio.run(service.run())
    except KeyboardInterrupt:
        return 130
    return 0
