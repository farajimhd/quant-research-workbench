from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.market_sip.events import run_build_text_tokens as _text_token_launcher  # noqa: E402


def main() -> int:
    _text_token_launcher.SCRIPT = REPO_ROOT / "pipelines" / "market_sip" / "events" / "clickhouse_build_qwen_text_embeddings.py"
    args = sys.argv[1:]
    if not any(arg in {"--build-embeddings", "--no-build-embeddings", "--profile-embeddings-only", "--summary-only"} for arg in args):
        sys.argv.append("--build-embeddings")
    return _text_token_launcher.main()


if __name__ == "__main__":
    raise SystemExit(main())
