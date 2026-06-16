from __future__ import annotations

# Compatibility wrapper. The implementation moved to pipelines.market_sip.sample_cache.validate_event_sample_cache.
# Prefer running/importing the pipeline module directly.
from pipelines.market_sip.sample_cache.validate_event_sample_cache import *  # noqa: F401,F403

if __name__ == "__main__":
    from pipelines.market_sip.sample_cache.validate_event_sample_cache import main

    main()
