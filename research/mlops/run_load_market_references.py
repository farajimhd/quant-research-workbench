from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULTS = {
    "database": "market_sip_compact",
    "reference_dir": str(Path(__file__).resolve().parents[1] / "market_references" / "massive"),
    "storage_policy": "",
    "rebuild": True,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launcher for ClickHouse market reference dense-id loader.")
    parser.add_argument("--database", default=DEFAULTS["database"])
    parser.add_argument("--reference-dir", default=DEFAULTS["reference_dir"])
    parser.add_argument("--storage-policy", default=DEFAULTS["storage_policy"])
    parser.add_argument("--no-rebuild", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    script = Path(__file__).with_name("clickhouse_load_market_references.py")
    command = [
        sys.executable,
        "-u",
        str(script),
        "--database",
        args.database,
        "--reference-dir",
        args.reference_dir,
    ]
    if args.storage_policy:
        command.extend(["--storage-policy", args.storage_policy])
    if args.no_rebuild:
        command.append("--no-rebuild")
    print("Equivalent command:", flush=True)
    print(" ".join(f'"{part}"' if " " in part else part for part in command), flush=True)
    raise SystemExit(subprocess.call(command))


if __name__ == "__main__":
    main()
