from __future__ import annotations

# Compatibility wrapper. The implementation moved to pipelines.market_sip.ingest.clickhouse_ingest_sip_compact_codec.
# Prefer running/importing the pipeline module directly.
from pipelines.market_sip.ingest.clickhouse_ingest_sip_compact_codec import *  # noqa: F401,F403

if __name__ == "__main__":
    from pipelines.market_sip.ingest.clickhouse_ingest_sip_compact_codec import main

    main()
