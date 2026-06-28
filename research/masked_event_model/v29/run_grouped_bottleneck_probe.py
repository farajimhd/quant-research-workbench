from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULT_RUN_PREFIX = "v29-groupedbottleneck128-1shard5ep"
DEFAULT_EMBEDDING_DIM = 128
DEFAULT_BATCH_SIZE = 4096
DEFAULT_EPOCHS = 5
DEFAULT_PROBE_EPOCHS = 5


def main() -> int:
    args, passthrough = parse_args()
    command = [
        sys.executable,
        str(Path(__file__).with_name("run_embedding_precision_probe.py")),
        "--only",
        selected_variant(args),
        "--run-prefix",
        args.run_prefix,
        "--batch-size",
        str(args.batch_size),
        "--epochs",
        str(args.epochs),
        "--probe-epochs",
        str(args.probe_epochs),
    ]
    if args.print_only:
        command.append("--print-only")
    if args.skip_existing_pretrain:
        command.append("--skip-existing-pretrain")
    command.extend(passthrough)

    print("=" * 104, flush=True)
    print(f"v29 grouped-bottleneck emb{args.embedding_dim} benchmark", flush=True)
    print("pretrain: one x-only shard, 5 epochs by default", flush=True)
    print("probe: temporal v1 labeled-cache linear probe, same W&B projects as v20 capacity tests", flush=True)
    print("=" * 104, flush=True)
    print(subprocess.list2cmdline(command), flush=True)
    return int(subprocess.run(command, check=False).returncode)


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description=(
            "Run the single v29 grouped-bottleneck comparison: emb128 BF16 by default "
            "pretraining on one shard, then the same temporal v1 linear probe."
        )
    )
    parser.add_argument("--run-prefix", default=DEFAULT_RUN_PREFIX)
    parser.add_argument("--embedding-dim", type=int, choices=(32, 128), default=DEFAULT_EMBEDDING_DIM)
    parser.add_argument("--bottleneck-force-fp32", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS)
    parser.add_argument("--probe-epochs", type=int, default=DEFAULT_PROBE_EPOCHS)
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--skip-existing-pretrain", action="store_true")
    parser.epilog = (
        "Any additional arguments are forwarded to run_embedding_precision_probe.py, "
        "for example --wandb-mode offline or --pretrain-cache-root <path>."
    )
    return parser.parse_known_args()


def selected_variant(args: argparse.Namespace) -> str:
    suffix = "fp32bottleneck" if args.bottleneck_force_fp32 else "bf16"
    return f"emb{int(args.embedding_dim)}-{suffix}"


if __name__ == "__main__":
    raise SystemExit(main())
