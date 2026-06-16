from __future__ import annotations

# Compatibility wrapper. The implementation moved to pipelines.market_sip.benchmarks.clickhouse_benchmark_events_span_batch.
# Prefer running/importing the pipeline module directly.
from pipelines.market_sip.benchmarks.clickhouse_benchmark_events_span_batch import *  # noqa: F401,F403

if __name__ == "__main__":
    from pipelines.market_sip.benchmarks.clickhouse_benchmark_events_span_batch import main

    main()
