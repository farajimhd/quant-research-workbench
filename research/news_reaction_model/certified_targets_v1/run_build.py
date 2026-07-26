from __future__ import annotations

import subprocess
import sys

from research.news_reaction_model.certified_targets_v1.build import main


if __name__ == "__main__":
    print(
        "COMMAND",
        subprocess.list2cmdline(
            [
                sys.executable,
                "-m",
                "research.news_reaction_model.certified_targets_v1.build",
                *sys.argv[1:],
            ]
        ),
        flush=True,
    )
    raise SystemExit(main(sys.argv[1:]))
