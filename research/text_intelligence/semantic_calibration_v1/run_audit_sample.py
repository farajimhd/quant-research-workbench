from __future__ import annotations

import argparse
import json
from pathlib import Path

from .audit import audit_sample
from .run_prepare_sample import default_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit a blinded semantic sample.")
    parser.add_argument("--root", type=Path, default=default_root())
    args = parser.parse_args(argv)
    report = audit_sample(args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
