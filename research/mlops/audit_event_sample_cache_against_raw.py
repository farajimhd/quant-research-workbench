from __future__ import annotations

# Compatibility wrapper. The implementation moved to pipelines.market_sip.sample_cache.audit_event_sample_cache_against_raw.
# Prefer running/importing the pipeline module directly.
from pipelines.market_sip.sample_cache.audit_event_sample_cache_against_raw import *  # noqa: F401,F403

if __name__ == "__main__":
    from pipelines.market_sip.sample_cache.audit_event_sample_cache_against_raw import main

    main()
