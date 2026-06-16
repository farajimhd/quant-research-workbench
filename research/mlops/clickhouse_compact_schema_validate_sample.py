from __future__ import annotations

# Compatibility wrapper. The implementation moved to pipelines.market_sip.validation.clickhouse_compact_schema_validate_sample.
# Prefer running/importing the pipeline module directly.
from pipelines.market_sip.validation.clickhouse_compact_schema_validate_sample import *  # noqa: F401,F403

if __name__ == "__main__":
    from pipelines.market_sip.validation.clickhouse_compact_schema_validate_sample import main

    main()
