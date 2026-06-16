from __future__ import annotations

# Compatibility wrapper. The implementation moved to pipelines.market_sip.ingest.clickhouse_quote_ingest_profile.
# Prefer running/importing the pipeline module directly.
from pipelines.market_sip.ingest.clickhouse_quote_ingest_profile import *  # noqa: F401,F403

if __name__ == "__main__":
    from pipelines.market_sip.ingest.clickhouse_quote_ingest_profile import main

    main()
