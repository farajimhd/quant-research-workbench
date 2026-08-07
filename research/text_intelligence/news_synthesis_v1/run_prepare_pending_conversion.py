from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.text_intelligence.news_synthesis_v1.pending_conversion import (
    default_output_root,
    prepare_pending_conversion,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare non-authoritative V1 review candidates for all uncertified manual-gold articles."
    )
    parser.add_argument("--output-root", type=Path, default=default_output_root())
    args = parser.parse_args()
    manifest = prepare_pending_conversion(args.output_root)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

