from __future__ import annotations

from .persistence import parse_args, run


if __name__ == "__main__":
    raise SystemExit(0 if run(parse_args()) is not None else 1)
