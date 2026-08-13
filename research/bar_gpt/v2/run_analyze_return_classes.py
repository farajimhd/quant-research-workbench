from __future__ import annotations

import os
import shlex
import sys

from research.bar_gpt.v2.analyze_return_classes import main


if __name__ == "__main__":
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    arguments = sys.argv[1:]
    command = [
        sys.executable,
        "-B",
        "-m",
        "research.bar_gpt.v2.analyze_return_classes",
        *arguments,
    ]
    print("Equivalent command: " + " ".join(shlex.quote(value) for value in command), flush=True)
    raise SystemExit(main(arguments))
