"""Compatibility wrapper for the moved Benzinga pipeline module."""

from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipelines.news.benzinga.news_benzinga_url_enrich import *  # noqa: F401,F403
from pipelines.news.benzinga.news_benzinga_url_enrich import main as _main


if __name__ == "__main__":
    _main()
