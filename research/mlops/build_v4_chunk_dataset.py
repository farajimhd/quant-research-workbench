from __future__ import annotations

# Compatibility wrapper. The implementation moved to pipelines.market_sip.legacy.build_v4_chunk_dataset.
# Prefer running/importing the pipeline module directly.
from pipelines.market_sip.legacy.build_v4_chunk_dataset import *  # noqa: F401,F403

if __name__ == "__main__":
    from pipelines.market_sip.legacy.build_v4_chunk_dataset import main

    main()
