from __future__ import annotations

import sys

from pipelines.news.benzinga.news_benzinga_rendered_v2_rebuild import main


DEFAULT_ARGS = ["--execute"]


if __name__ == "__main__":
    args = sys.argv[1:] or DEFAULT_ARGS
    print(
        "COMMAND python -m pipelines.news.benzinga.run_news_rendered_v2_rebuild "
        + " ".join(args),
        flush=True,
    )
    raise SystemExit(main(args))
