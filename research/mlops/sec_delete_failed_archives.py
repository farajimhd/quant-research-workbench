"""Compatibility wrapper for the moved SEC EDGAR pipeline module."""

from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipelines.sec.edgar.sec_delete_failed_archives import *  # noqa: F401,F403
from pipelines.sec.edgar.sec_delete_failed_archives import main as _main


if __name__ == "__main__":
    _main()
