from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "pipelines").exists())
SCRIPT = REPO_ROOT / "pipelines" / "news" / "benzinga" / "news_reaction_finalize.py"


def main(argv: list[str] | None = None) -> int:
    command = [sys.executable, str(SCRIPT), *(argv if argv is not None else sys.argv[1:])]
    print("COMMAND", subprocess.list2cmdline(command), flush=True)
    return subprocess.call(command, cwd=REPO_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
