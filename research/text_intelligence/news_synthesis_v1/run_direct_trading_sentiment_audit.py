from __future__ import annotations

import argparse
import json
from pathlib import Path

from .direct_trading_sentiment_audit import generate_audit


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the mismatch-only direct-trading sentiment audit."
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--previous-manifest", type=Path)
    parser.add_argument(
        "--population-ids",
        type=Path,
        help="Optional JSON array of certified sample IDs for an identity-stable audit.",
    )
    args = parser.parse_args()
    manifest = generate_audit(
        args.output_root.resolve(),
        previous_manifest=(
            args.previous_manifest.resolve() if args.previous_manifest else None
        ),
        population_ids=(
            json.loads(args.population_ids.read_text(encoding="utf-8"))
            if args.population_ids
            else None
        ),
    )
    print(
        json.dumps(
            {
                "version": manifest["version"],
                "identity_authority": manifest["identity_authority"],
                "population": manifest["population"],
                "missing_dispositions": manifest["missing_dispositions"],
                "engine_failures": manifest["engine_failures"],
                "comparison_to_previous": manifest.get("comparison_to_previous"),
            },
            indent=2,
        )
    )
    return 0 if not manifest["engine_failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
