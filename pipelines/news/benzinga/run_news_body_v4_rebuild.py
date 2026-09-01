from __future__ import annotations

import sys

from pipelines.news.benzinga.news_benzinga_body_v3_rebuild import main


if __name__ == "__main__":
    args = [*sys.argv[1:], "--body-version", "v4"]
    print("COMMAND python -m pipelines.news.benzinga.run_news_body_v4_rebuild " + " ".join(sys.argv[1:]), flush=True)
    raise SystemExit(main(args))
