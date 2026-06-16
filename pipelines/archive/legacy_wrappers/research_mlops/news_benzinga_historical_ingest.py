"""Compatibility wrapper for the moved Benzinga pipeline module."""

from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipelines.archive.legacy_workflows.news_benzinga.news_benzinga_historical_ingest import *  # noqa: F401,F403
from pipelines.archive.legacy_workflows.news_benzinga.news_benzinga_historical_ingest import main as _main


if __name__ == "__main__":
    _main()
