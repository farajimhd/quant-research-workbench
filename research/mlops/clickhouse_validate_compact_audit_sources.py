from __future__ import annotations

# Compatibility wrapper. The implementation moved to pipelines.market_sip.validation.clickhouse_validate_compact_audit_sources.
# Prefer running/importing the pipeline module directly.
from pipelines.market_sip.validation.clickhouse_validate_compact_audit_sources import *  # noqa: F401,F403

if __name__ == "__main__":
    from pipelines.market_sip.validation.clickhouse_validate_compact_audit_sources import main

    main()
