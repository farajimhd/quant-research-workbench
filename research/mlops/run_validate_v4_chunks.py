from __future__ import annotations

# Compatibility wrapper. The implementation moved to pipelines.market_sip.legacy.run_validate_v4_chunks.
# Prefer running/importing the pipeline module directly.
from pipelines.market_sip.legacy.run_validate_v4_chunks import *  # noqa: F401,F403

if __name__ == "__main__":
    from pipelines.market_sip.legacy.run_validate_v4_chunks import main

    main()
