"""Launch the backend with a Windows socket loop that survives peer resets."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import ssl
import sys


sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _restore_stdlib_ssl_for_keeper() -> None:
    """Undo interpreter-wide truststore injection before backend threads start."""
    if not ssl.SSLContext.__module__.startswith(
        ("truststore.", "pip._vendor.truststore.")
    ):
        return
    try:
        from truststore import extract_from_ssl
    except ImportError:
        from pip._vendor.truststore import extract_from_ssl
    extract_from_ssl()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Quant Workbench backend API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    # pip-system-certs replaces ssl.SSLContext at interpreter startup on some
    # Windows installations. Kazoo's Keeper TLS socket is incompatible with
    # that wrapper; restore the standard context before any backend worker or
    # network client starts. Keeper still verifies its pinned CA and hostname.
    _restore_stdlib_ssl_for_keeper()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    import uvicorn

    uvicorn.run(
        "src.backend.app:app",
        host=args.host,
        port=args.port,
        lifespan="on",
        reload=args.reload,
        reload_dirs=[str(REPO_ROOT / "src")] if args.reload else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
