from __future__ import annotations

# Compatibility wrapper. The implementation moved to pipelines.market_sip.validation.clickhouse_delete_compact_audit_rows.
# Prefer running/importing the pipeline module directly.
from pipelines.market_sip.validation.clickhouse_delete_compact_audit_rows import *  # noqa: F401,F403

if __name__ == "__main__":
    from pipelines.market_sip.validation.clickhouse_delete_compact_audit_rows import main

    main()
