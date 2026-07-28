from __future__ import annotations

from .audit import parse_args, run


if __name__ == "__main__":
    raise SystemExit(0 if run(parse_args()) else 1)
