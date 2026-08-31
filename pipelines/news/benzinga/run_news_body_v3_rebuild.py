from __future__ import annotations

import sys

from pipelines.news.benzinga.news_benzinga_body_v3_rebuild import main


DEFAULT_ARGS = ["rebuild", "--limit-days", "1"]


if __name__ == "__main__":
    args = sys.argv[1:] or DEFAULT_ARGS
    print("COMMAND python -m pipelines.news.benzinga.run_news_body_v3_rebuild " + " ".join(args), flush=True)
    raise SystemExit(main(args))
