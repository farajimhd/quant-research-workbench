from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download open-source news intelligence models.")
    parser.add_argument("--manifest", default=Path(__file__).resolve().parents[1] / "models" / "opensource_models.json")
    parser.add_argument("--root", default=None, help="Target artifact root. Defaults to manifest artifact_root.")
    parser.add_argument("--include-large", action="store_true", help="Include large LLM/offline research models.")
    parser.add_argument("--include-gated", action="store_true", help="Attempt gated models that require accepted licenses/auth.")
    parser.add_argument("--only", nargs="*", default=None, help="Download only these manifest keys.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest)
    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    root = Path(args.root or manifest["artifact_root"])
    root.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("huggingface_hub is required. Install with: pip install huggingface_hub", file=sys.stderr)
        return 2

    failures: list[tuple[str, str]] = []
    for item in manifest["models"]:
        key = item["key"]
        if args.only and key not in args.only:
            continue
        if not item.get("download_by_default", False) and not args.include_large and not args.include_gated:
            print(f"SKIP {key}: not enabled by default")
            continue
        if item.get("large", False) and not args.include_large:
            print(f"SKIP {key}: large model, pass --include-large")
            continue
        if item.get("gated", False) and not args.include_gated:
            print(f"SKIP {key}: gated model, pass --include-gated after accepting license/access")
            continue
        target = root / key
        print(f"DOWNLOAD {key} -> {target}")
        try:
            snapshot_download(
                repo_id=item["repo_id"],
                local_dir=str(target),
                local_dir_use_symlinks=False,
                resume_download=True,
            )
        except Exception as error:  # pragma: no cover - network/auth dependent
            print(f"FAIL {key}: {error}", file=sys.stderr)
            failures.append((key, str(error)))
    if failures:
        print("Download failures:")
        for key, error in failures:
            print(f"- {key}: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

