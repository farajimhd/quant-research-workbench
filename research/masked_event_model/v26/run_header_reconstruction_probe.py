from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULT_RUN_PREFIX = "v26-headerrecon-emb32-1shard5ep"
DEFAULT_BATCH_SIZE = 4096
DEFAULT_EPOCHS = 5
DEFAULT_PROBE_EPOCHS = 5
DEFAULT_HEADER_LOSS_WEIGHT = 0.25


def main() -> int:
    args, passthrough = parse_args()
    command = [
        sys.executable,
        str(Path(__file__).with_name("run_embedding_precision_probe.py")),
        "--only",
        "emb32-bf16",
        "--run-prefix",
        args.run_prefix,
        "--batch-size",
        str(args.batch_size),
        "--epochs",
        str(args.epochs),
        "--probe-epochs",
        str(args.probe_epochs),
        "--header-loss-weight",
        str(args.header_loss_weight),
    ]
    if args.print_only:
        command.append("--print-only")
    if args.skip_existing_pretrain:
        command.append("--skip-existing-pretrain")
    command.extend(passthrough)

    print("=" * 104, flush=True)
    print("v26 header-reconstruction emb32 benchmark", flush=True)
    print("pretrain: one x-only shard, header+masked-event reconstruction, 5 epochs by default", flush=True)
    print("probe: temporal v1 labeled-cache linear probe from the resulting v26 encoder checkpoint", flush=True)
    print("=" * 104, flush=True)
    print(subprocess.list2cmdline(command), flush=True)
    return int(subprocess.run(command, check=False).returncode)


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description=(
            "Run the v26 header-reconstruction experiment: emb32 BF16 "
            "pretraining on one shard, then the same temporal v1 linear probe."
        )
    )
    parser.add_argument("--run-prefix", default=DEFAULT_RUN_PREFIX)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS)
    parser.add_argument("--probe-epochs", type=int, default=DEFAULT_PROBE_EPOCHS)
    parser.add_argument("--header-loss-weight", type=float, default=DEFAULT_HEADER_LOSS_WEIGHT)
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--skip-existing-pretrain", action="store_true")
    parser.epilog = (
        "Any additional arguments are forwarded to run_embedding_precision_probe.py, "
        "for example --wandb-mode offline, --pretrain-cache-root <path>, or "
        "--validation-frequency-samples 1048576."
    )
    return parser.parse_known_args()


if __name__ == "__main__":
    raise SystemExit(main())
