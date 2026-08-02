from __future__ import annotations

import os
import shlex
import sys

from research.bar_gpt.v1.linear_probe import main


if __name__ == "__main__":
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    args = sys.argv[1:]
    command = [sys.executable, "-B", "-m", "research.bar_gpt.v1.linear_probe", *args]
    print("Equivalent command: " + " ".join(shlex.quote(item) for item in command), flush=True)
    raise SystemExit(main(args))

