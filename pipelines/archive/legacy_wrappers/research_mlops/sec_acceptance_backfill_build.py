"""Compatibility wrapper for the moved SEC EDGAR pipeline module."""

from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipelines.sec.edgar.sec_acceptance_backfill_build import *  # noqa: F401,F403
from pipelines.sec.edgar.sec_acceptance_backfill_build import main as _main


if __name__ == "__main__":
    _main()
