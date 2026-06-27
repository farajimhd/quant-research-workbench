from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULT_RUN_PREFIX = "v20-largeencoder-1shard5ep"
DEFAULT_BATCH_SIZE = 4096
DEFAULT_EPOCHS = 5
DEFAULT_PROBE_EPOCHS = 5

# Keep this aligned with the `large` preset in train_model_size_sweep.py.
LARGE_ENCODER = {
    "d_byte": 64,
    "d_model": 384,
    "n_heads": 12,
    "encoder_layers": 12,
    "decoder_layers": 5,
    "ffn_mult": 4,
    "dropout": 0.08,
}


def main() -> int:
    args, passthrough = parse_args()
    command = [
        sys.executable,
        str(Path(__file__).with_name("run_embedding_precision_probe.py")),
        "--only",
        "emb32-bf16,emb128-bf16",
        "--run-prefix",
        args.run_prefix,
        "--batch-size",
        str(args.batch_size),
        "--epochs",
        str(args.epochs),
        "--probe-epochs",
        str(args.probe_epochs),
        "--d-byte",
        str(LARGE_ENCODER["d_byte"]),
        "--d-model",
        str(LARGE_ENCODER["d_model"]),
        "--n-heads",
        str(LARGE_ENCODER["n_heads"]),
        "--encoder-layers",
        str(LARGE_ENCODER["encoder_layers"]),
        "--decoder-layers",
        str(LARGE_ENCODER["decoder_layers"]),
        "--ffn-mult",
        str(LARGE_ENCODER["ffn_mult"]),
        "--dropout",
        str(LARGE_ENCODER["dropout"]),
    ]
    if args.print_only:
        command.append("--print-only")
    if args.skip_existing_pretrain:
        command.append("--skip-existing-pretrain")
    command.extend(passthrough)

    print("=" * 104, flush=True)
    print("v20 large-encoder capacity benchmark", flush=True)
    print("pretrain: large encoder, one x-only shard, 5 epochs by default", flush=True)
    print("variants: emb32 BF16 and emb128 BF16, followed by the same temporal v1 linear probe", flush=True)
    print(f"large_encoder={LARGE_ENCODER}", flush=True)
    print("=" * 104, flush=True)
    print(subprocess.list2cmdline(command), flush=True)
    return int(subprocess.run(command, check=False).returncode)


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description=(
            "Run the v20 large-encoder capacity comparison: pretrain large emb32 "
            "and large emb128, then linear-probe both checkpoints."
        )
    )
    parser.add_argument("--run-prefix", default=DEFAULT_RUN_PREFIX)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS)
    parser.add_argument("--probe-epochs", type=int, default=DEFAULT_PROBE_EPOCHS)
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
