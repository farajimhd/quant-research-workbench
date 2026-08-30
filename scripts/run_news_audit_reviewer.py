from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
)
from research.mlops.env import load_env_files  # noqa: E402
from tools.news_audit_reviewer.core import (  # noqa: E402
    DEFAULT_ASSIGNMENTS,
    DEFAULT_EVALUATION,
    ClickHouseReviewBackend,
)
from tools.news_audit_reviewer.server import create_app  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the standalone ClickHouse-backed News Synthesis reviewer."
    )
    parser.add_argument("--database", default="q_live")
    parser.add_argument("--evaluation", type=Path, default=DEFAULT_EVALUATION)
    parser.add_argument("--assignments", type=Path, default=DEFAULT_ASSIGNMENTS)
    parser.add_argument("--port", type=int, default=8812)
    parser.add_argument("--prepare-only", action="store_true", help="Prepare and validate ClickHouse, then exit.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the reviewer in a browser.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.port <= 65_535:
        raise SystemExit("--port must be between 1 and 65535")
    load_env_files(discover_clickhouse_env_files(), verbose=False)
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
        timeout_seconds=60,
    )
    backend = ClickHouseReviewBackend(
        client,
        database=args.database,
        evaluation_path=args.evaluation,
        assignments_path=args.assignments,
    )
    print("News Synthesis ClickHouse Reviewer", flush=True)
    print(f"  Database   : {args.database}", flush=True)
    print("  Preparing  : lineage-bound 31,856-row training source snapshot", flush=True)
    prepared = backend.prepare_source()
    print(
        f"  Ready      : articles={prepared['articles']:,} paths={prepared['paths']:,} "
        f"groups={prepared['groups']:,} holdout={prepared['holdout_rows']}",
        flush=True,
    )
    if args.prepare_only:
        return 0
    app = create_app(backend)
    url = f"http://127.0.0.1:{args.port}"
    print(f"  URL        : {url}", flush=True)
    print("  Persistence: append-only operator decisions in ClickHouse", flush=True)
    print("  Stop       : Ctrl+C", flush=True)
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
