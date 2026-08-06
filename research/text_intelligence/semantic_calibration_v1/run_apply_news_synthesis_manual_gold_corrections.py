from __future__ import annotations

import json

from .news_synthesis_manual_gold_corrections import apply_manual_gold_corrections


def main() -> int:
    print(json.dumps(apply_manual_gold_corrections(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
