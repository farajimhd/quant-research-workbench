"""Launch the frozen-plan ordinal/coarse campaign without writing bytecode."""

import os
from pathlib import Path
import sys
import subprocess
import json
import time

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ.setdefault("POLARS_MAX_THREADS", "2")
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from research.bar_gpt.v5.shards.balanced_build import main as build_main, arguments
from research.bar_gpt.v5.shards.contract import write_json
from research.bar_gpt.v5.shards import balanced_build as builder
from research.bar_gpt.v5.shard_runtime_v1 import scheduling_runtime


def canonical_arguments(argv):
    """Make an explicitly supplied default use the builder's exact JSON scalar type.

    The immutable v6 producer has an integer default of 1 for a float CLI option.
    Its migration contract therefore hashes 1, whereas --query-memory-gib 1 hashes
    1.0. Remove only an equivalent default override; the effective memory limit,
    producer code and sealed contract stay unchanged. Nondefault limits still
    reach the producer and must pass its ordinary contract check.
    """
    selected = arguments(argv).query_memory_gib
    default = arguments([]).query_memory_gib
    if selected != default or type(selected) is type(default):
        return list(argv)
    normalized, skip_value = [], False
    for item in argv:
        if skip_value:
            skip_value = False
        elif item == "--query-memory-gib":
            skip_value = True
        elif not item.startswith("--query-memory-gib="):
            normalized.append(item)
    print("Canonicalized equivalent 1 GiB default: preserve sealed integer 1 instead of float 1.0.", flush=True)
    return normalized


def main(argv=None):
    """Run the pinned producer and expose startup/controller failures to its observer.

    Output is the dataset's progress.json, not a replacement data receipt. Errors
    acquiring a lock or filesystem permission never overwrite another controller's
    progress. The original exception still propagates to the supervisor's stderr.
    """
    argv = canonical_arguments(sys.argv[1:] if argv is None else argv)
    output = arguments(argv).output
    try:
        with scheduling_runtime(builder, output):
            return build_main(argv)
    except (PermissionError, BlockingIOError):
        raise  # A second launcher must not mark the actual lock owner's progress failed.
    except (Exception, KeyboardInterrupt) as error:
        try:
            path = output / "progress.json"
            snapshot = json.loads(path.read_text()) if path.exists() else {}
            failure = dict(stage="controller/startup", error=f"{type(error).__name__}: {error}")
            snapshot.update(state="stopped" if isinstance(error, KeyboardInterrupt) else "failed",
                            updated=time.time(), error=failure["error"],
                            failed=[*snapshot.get("failed", []), failure])
            write_json(path, snapshot)  # Preserve existing durable counters while exposing the failure.
        except (OSError, ValueError) as status_error:
            print(f"Could not publish terminal status: {status_error}", file=sys.stderr, flush=True)
        raise

if __name__ == "__main__":
    command = subprocess.list2cmdline(
        [sys.executable, "-B", "-u", str(Path(__file__).resolve()), *sys.argv[1:]]
    )
    print("Equivalent: " + command, flush=True)
    raise SystemExit(main())
