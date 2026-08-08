from __future__ import annotations

import argparse
from pathlib import Path

from .sol_teacher_forecast_gold_amendment import amend_reviewed_audit_gold


def main() -> None:
    parser = argparse.ArgumentParser(description="Amend reviewed Sol forecast gold")
    parser.add_argument("--base-root", type=Path, required=True)
    parser.add_argument("--amendments", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    manifest = amend_reviewed_audit_gold(
        args.base_root.resolve(), args.amendments.resolve(), args.output_root.resolve()
    )
    print(
        f"AMENDED_GOLD units={manifest['population']['issuer_units']:,} "
        f"amendments={manifest['population']['amendments']:,}"
    )


if __name__ == "__main__":
    main()
