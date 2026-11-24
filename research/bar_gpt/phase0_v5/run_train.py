"""Launch Phase0 V5 against the certified full Phase1 split; never raw flatfiles."""

import os

# Set this before repository imports so launching from a snapshot cannot create
# bytecode caches there. Every generated artifact belongs in the run directory.
os.environ.update(PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")

import argparse
import copy
import json
import random
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
import numpy as np
import torch

from ..phase1_v8.runtime import preflight
from ..phase1_v11.data import verify
from ..phase1_v26.reporting import Reporter, ReportingFailure
from ..phase2_v2.train import lease, load_checkpoint
from ..v5.data_v3 import load_split
from ..v5.loader_index_v1 import attach
from ..v5.shards.phase1_contract import file_hash, write_json
from research.mlops.env import discover_env_files, load_env_files
from .model import Autoencoder, Config
from .transforms import TRANSFORM_VERSION
from .train import train


def parser():
    """Keep scientific choices explicit and operational defaults bounded."""
    result = argparse.ArgumentParser(description=__doc__)
    root = Path("D:/TradingML/runtimes/bar_gpt")
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--source-revision", required=True)
    result.add_argument("--calibration", type=Path, required=True)
    result.add_argument(
        "--data", type=Path, default=root / "phase1-v8-learned-mae-20261117/dataset"
    )
    result.add_argument(
        "--split", type=Path, default=root / "phase1-v6-cohort-balanced-v4/split.json"
    )
    result.add_argument("--resume", type=Path)
    result.add_argument("--precision", choices=("fp32", "bf16"), default="fp32")
    result.add_argument("--precision-fork", action="store_true", help="Start a separate BF16 experiment from a verified FP32 checkpoint")
    result.add_argument("--precision-comparison", type=Path, help="Verified profiling directory containing the matched FP32 development report")
    result.add_argument("--engineering-resume", action="store_true", help="Permit only the qualified FP32 execution transition; preserve scientific contract")
    result.add_argument("--training-panel-metrics", action="store_true", help="Evaluate a fixed train-only sample alongside development")
    result.add_argument("--graph-validation", action="store_true", help="Qualified complete-metric CUDA replay; retain original validation batch size")
    result.add_argument("--epochs", type=int, default=20)
    result.add_argument(
        "--batch-size", type=int, default=256, help="Windows; each contains64 independent bars"
    )
    result.add_argument("--workers", type=int, default=8)
    result.add_argument("--validation-batch-size", type=int, default=256)
    result.add_argument("--validation-workers", type=int, default=8)
    result.add_argument("--prefetch", type=int, default=4)
    result.add_argument("--learning-rate", type=float, default=1e-4)
    result.add_argument("--min-learning-rate", type=float, default=1e-6)
    result.add_argument("--validation-every", type=int, default=1000)
    result.add_argument("--checkpoint-every", type=int, default=500)
    result.add_argument("--log-every", type=int, default=25)
    result.add_argument(
        "--max-updates", type=int, default=0, help="0=full run; positive=bounded smoke/profile"
    )
    result.add_argument("--seed", type=int, default=17)
    result.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    result.add_argument("--wandb", choices=("online", "disabled"), default="online")
    result.add_argument("--embedding", type=int, default=16)
    result.add_argument("--width", type=int, default=128)
    result.add_argument("--layers", type=int, default=2)
    result.add_argument("--heads", type=int, default=4)
    result.add_argument("--chunk-bars", type=int, default=262144)
    result.add_argument(
        "--optimized",
        action="store_true",
        help="CUDA graph replay for the residual MLP",
    )
    return result


def verify_engineering_transition(previous, current):
    """Allow source/chunk/kernel changes, never data, architecture or optimizer choices."""
    old, new = copy.deepcopy(previous), copy.deepcopy(current)
    if old.get("precision") != "FP32" or new.get("execution") != "fp32_residual_mlp_cuda_graph_v1":
        raise ValueError("Only the qualified FP32 transition is allowed")
    if old.get("execution") not in (None, new["execution"]):
        raise ValueError("Unknown previous execution mode")
    for contract in (old, new):
        if contract.get("validation_execution") not in (None, "cuda_graph_all_metrics_v1"):
            raise ValueError("Unknown validation execution mode")
        contract.pop("validation_execution", None)
        contract.pop("source", None)
        contract.pop("execution", None)
        contract["config"].pop("chunk_bars", None)
    if old != new:
        raise ValueError("Engineering resume cannot change the scientific/traversal contract")


def main():
    """Verify authority/ownership before opening the trainer or W&B writer."""
    args = parser().parse_args()
    positive = (
        args.epochs,
        args.batch_size,
        args.validation_batch_size,
        args.prefetch,
        args.log_every,
        args.validation_every,
        args.checkpoint_every,
    )
    if min(positive) <= 0 or args.max_updates < 0:
        raise ValueError("Positive batch/interval sizes and nonnegative update limit required")
    if not 0 <= args.workers <= 32 or not 0 <= args.validation_workers <= 32:
        raise ValueError("Workers must be between0 and32")
    if args.prefetch > 8:
        raise ValueError("Keep per-worker prefetch bounded to8 or fewer batches")
    if not 0 < args.min_learning_rate <= args.learning_rate:
        raise ValueError("Require0 < minimum LR <= peak LR")
    if args.precision_fork:
        if not args.resume or args.precision != "bf16" or not args.optimized or args.engineering_resume or not args.training_panel_metrics:
            raise ValueError("Precision fork requires BF16, optimized execution and a parent checkpoint")
        if args.output.exists():
            raise ValueError("A precision fork requires a fresh output directory")
    if args.precision == "bf16" and not args.optimized:
        raise ValueError("BF16 requires the profiled optimized block path")
    if args.graph_validation and args.device != "cuda":
        raise ValueError("Validation graph requires CUDA")
    if args.resume and not args.precision_fork and args.resume.resolve().parent != args.output.resolve():
        raise ValueError("Resume in the SAME run directory to preserve identity")
    args.preparation = "raw_gpu"
    args.shard_prefetch = 16
    args.shard_prefetch_mib = 64
    print(
        subprocess.list2cmdline(
            [sys.executable, "-B", "-m", "research.bar_gpt.phase0_v5.run_train", *sys.argv[1:]]
        ),
        flush=True,
    )
    # Both exact binaries were verified as Microsoft-signed Windows UI processes
    # on this workstation. They are not training owners; no wildcard is allowed.
    preflight(
        args,
        desktop_paths=(
            r"c:\windows\system32\applicationframehost.exe",
            r"c:\windows\immersivecontrolpanel\systemsettings.exe",
        ),
    )
    torch.set_num_threads(4)
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    np.random.seed(args.seed)
    calibration = json.loads(args.calibration.read_text())
    frozen = verify(args.data)
    manifest = load_split(args.split)
    if (
        calibration["version"] != TRANSFORM_VERSION
        or calibration["data_hash"] != frozen["hash"]
        or calibration["split_hash"] != manifest["manifest_hash"]
    ):
        raise ValueError("Calibration/data/split identity differs")
    manifest = attach(manifest, Path(manifest["dataset"]))
    manifest["splits"] = {}
    config = Config(
        embedding=args.embedding,
        width=args.width,
        layers=args.layers,
        heads=args.heads,
        chunk_bars=args.chunk_bars,
    )
    model = Autoencoder(calibration["parameters"], config).to(args.device)
    if args.optimized:
        if args.device != "cuda":
            raise ValueError("Optimized replay requires CUDA")
        model.configure_execution("math", args.precision, summary_only=True)
    contract = dict(
        version=TRANSFORM_VERSION,
        architecture="phase0-v5-residual-mlp-81-input",
        source=args.source_revision,
        config=asdict(config),
        calibration_sha256=file_hash(args.calibration),
        split_sha256=file_hash(args.split),
        split_hash=manifest["manifest_hash"],
        subset_hash=frozen["hash"],
        counts=manifest["counts"],
        epochs=args.epochs,
        batch_size=args.batch_size,
        workers=args.workers,
        validation_batch_size=args.validation_batch_size,
        validation_workers=args.validation_workers,
        seed=args.seed,
        learning_rate=args.learning_rate,
        min_learning_rate=args.min_learning_rate,
        precision="FP32" if args.precision == "fp32" else "BF16 MLP hidden layers; FP32 residuals, norms, stem, embedding, decoder, objective and optimizer state",
        scheduler="cosine_by_consumed_windows",
        objective="equal price/activity SmoothL1 plus0.1 coordinate loss",
        selection="best fixed-development objective; inspect all quality metrics before promotion",
    )
    if args.optimized:
        contract["execution"] = f"{args.precision}_residual_mlp_cuda_graph_v1"
    if args.graph_validation:
        contract["validation_execution"] = "cuda_graph_all_metrics_v1"
    transition = None
    if args.precision_fork:
        peek = torch.load(args.resume, map_location="cpu", weights_only=False)
        previous = peek["contract"]
        saved = load_checkpoint(args.resume, previous)
        comparable = copy.deepcopy(contract)
        comparable["precision"] = "FP32"
        comparable["execution"] = "fp32_residual_mlp_cuda_graph_v1"
        verify_engineering_transition(previous, comparable)
        args.resume_contract = previous
        transition = dict(kind="new_precision_experiment", previous=previous, current=contract,
                          checkpoint_sha256=file_hash(args.resume), step=saved["step"],
                          preserved=["model", "optimizer", "rng", "cursor", "windows"],
                          reset="new experiment identity and best-selection baseline")
        del peek, saved
    if args.engineering_resume:
        if not args.resume or not args.optimized:
            raise ValueError("Engineering resume requires --resume and --optimized")
        peek = torch.load(args.resume, map_location="cpu", weights_only=False)
        previous = peek["contract"]
        saved = load_checkpoint(args.resume, previous)
        verify_engineering_transition(previous, contract)
        args.resume_contract = previous
        transition = dict(previous=previous, current=contract,
                          checkpoint_sha256=file_hash(args.resume), step=saved["step"],
                          preserved=["model", "optimizer", "rng", "cursor", "windows", "best"])
        del peek, saved
    if args.resume:
        load_checkpoint(args.resume, getattr(args, "resume_contract", contract))
    comparison = None
    if args.precision_comparison:
        if not args.precision_fork:
            raise ValueError("A matched precision comparison belongs to a new precision fork")
        profile = json.loads((args.precision_comparison / "profiles.json").read_text())
        if profile["checkpoint_sha"] != file_hash(args.resume):
            raise ValueError("Comparison checkpoint differs from the parent")
        comparison = json.loads((args.precision_comparison / "math-fp32-graph-development.json").read_text())
    identity_model = contract
    if args.resume and not args.precision_fork:
        # Preserve the founder's scientific W&B signature. Actual execution and
        # checkpoint contracts remain explicit and separately verified above.
        identity_model = json.loads((args.output / "manifest.json").read_text())["model"]
        if args.engineering_resume:
            verify_engineering_transition(identity_model, contract)
    run_config = dict(
        model=identity_model,
        execution_model=contract,
        output=str(args.output),
        resume=str(args.resume) if args.resume else None,
        arguments={
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(args).items()
        },
    )
    with lease(args.output):
        if transition:
            prefix = "precision-fork" if args.precision_fork else "engineering-transition"
            receipt = args.output / f"{prefix}-{transition['step']}-{args.source_revision[:9]}.json"
            if receipt.exists():
                raise FileExistsError("Transition receipt already exists; inspect before retrying")
            write_json(receipt, transition)
        load_env_files(discover_env_files(Path(__file__).resolve().parents[3]), verbose=False)
        os.environ.setdefault("WANDB_ENTITY", "mehdifaraji")
        reporter = Reporter(args.output, run_config, mode=args.wandb, project="bargpt-phase0-v5-mlp16")
        write_json(args.output / "manifest.json", run_config)
        try:
            if comparison:
                write_json(args.output / "comparison-fp32-development.json", comparison)
                reporter.log({f"comparison_fp32/{key}": value for key, value in comparison["metrics"].items()}, transition["step"])
            state = train(model, manifest, args, contract, reporter)
            # The shared reporter uses this established success label for bounded
            # work; passing a new label would incorrectly mark W&B as failed.
            reporter.finish("qualification_complete" if state == "bounded_complete" else state)
        except ReportingFailure as error:
            # Do not call a known-broken SDK again while closing local evidence.
            reporter.close_local("failed", repr(error))
            raise
        except BaseException as error:
            try:
                reporter.finish("failed", repr(error))
            except Exception:
                reporter.close_local("failed", repr(error))
            raise


if __name__ == "__main__":
    main()
