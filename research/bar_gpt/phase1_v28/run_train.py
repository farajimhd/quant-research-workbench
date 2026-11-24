"""Train the residual sequence mixer using immutable Phase0 and Phase1 authorities."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
import json
import random
import subprocess
import sys
from pathlib import Path
from dataclasses import asdict
import numpy as np
import torch
from ..phase0_v7.run_train import parser as base_parser
from ..phase0_v7.transforms import TRANSFORM_VERSION
from ..phase1_v8.runtime import preflight
from ..phase1_v11.data import verify
from ..phase1_v26.reporting import Reporter, ReportingFailure
from ..phase2_v2.train import lease, load_checkpoint
from ..v5.data_v3 import load_split
from ..v5.loader_index_v1 import attach
from ..v5.shards.phase1_contract import file_hash, write_json
from research.mlops.env import discover_env_files, load_env_files
from .model import Autoencoder, Config
from .eligibility import POLICY
from .train import train


def parser():
    result = base_parser()
    result.description = __doc__
    result.add_argument("--parent-checkpoint", type=Path, required=True)
    result.add_argument("--mixer-layers", type=int, default=4)
    result.add_argument("--mixer-hidden", type=int, default=128)
    result.add_argument("--mixer-chunk-windows", type=int, default=4096)
    result.set_defaults(batch_size=4096, validation_batch_size=1024, workers=16, log_every=100,
                        optimized=True, graph_validation=True, training_panel_metrics=True)
    return result


def build(args):
    """Verify every frozen dependency before constructing the trainable addition."""
    saved = torch.load(args.parent_checkpoint, map_location="cpu", weights_only=False)
    saved = load_checkpoint(args.parent_checkpoint, saved["contract"])
    parent = saved["contract"]
    if parent.get("version") != "phase1-v27-frozen-phase0-temporal-mixer-v1" or parent.get("precision") != "FP32":
        raise ValueError("Requires the FP32 Phase1 V27 parent")
    calibration = json.loads(args.calibration.read_text())
    frozen = verify(args.data)
    manifest = load_split(args.split)
    if (file_hash(args.calibration) != parent["calibration_sha256"]
        or file_hash(args.split) != parent["split_sha256"]
        or frozen["hash"] != parent["subset_hash"]
        or calibration["version"] != TRANSFORM_VERSION
        or calibration.get("eligibility_policy") != POLICY):
        raise ValueError("Parent calibration, split, or subset differs")
    manifest = attach(manifest, Path(manifest["dataset"]))
    manifest["splits"] = {}
    config = Config(**{**parent["config"], "chunk_bars": args.chunk_bars},mixer_chunk_windows=args.mixer_chunk_windows)
    model = Autoencoder(calibration["parameters"], config)
    model.load_previous(saved)
    model.configure_execution(precision=args.precision)
    model.to(args.device)
    # Continue the exact original data traversal. Loader worker count and batch
    # shape determine ordered batches, so changing them would invalidate cursor reuse.
    for key in ('epochs','batch_size','workers','seed'):
        if getattr(args,key) != parent[key]:
            raise ValueError(f"Continuation requires parent {key}={parent[key]}")
    args.starting_windows=saved['windows']
    args.parent_traversal={k:saved[k] for k in ('epoch','cursor','windows','epoch_windows','phase')}
    contract = dict(version="phase1-v28-masked-finetune-structural-v1", source=args.source_revision,
        architecture="64x32 residual MLP-Mixer; fine-tuned Phase0 encoder, frozen affine decoder",
        config=asdict(config), parent_checkpoint_sha256=file_hash(args.parent_checkpoint),
        parent_step=saved["step"], starting_windows=args.starting_windows, encoder_lr_factor=config.encoder_lr_factor, calibration_sha256=file_hash(args.calibration),
        split_sha256=file_hash(args.split), subset_hash=frozen["hash"], counts=manifest["counts"],
        epochs=args.epochs, batch_size=args.batch_size, workers=args.workers,
        validation_batch_size=args.validation_batch_size, validation_workers=args.validation_workers,
        seed=args.seed, learning_rate=args.learning_rate, min_learning_rate=args.min_learning_rate,
        scheduler="cosine_by_consumed_windows", precision="FP32", masking="4..8 random positions per window before temporal mixing",
        objective="equal hidden/visible reconstruction + .05 balanced direction + .05 log-range + .05 source-consistent OHLC", eligibility=POLICY, exported_shape=[64,32],
        trainable_parameters=sum(p.numel() for p in model.parameters() if p.requires_grad),
        frozen_parameters=sum(p.numel() for p in model.parameters() if not p.requires_grad))
    return model, manifest, contract


def main():
    args = parser().parse_args()
    # Precision/architecture forks require a separately qualified experiment.
    if args.precision != "fp32" or args.precision_fork or args.precision_comparison or args.engineering_resume:
        raise ValueError("This experiment preserves FP32; no unqualified execution forks")
    if not 0 < args.min_learning_rate <= args.learning_rate:
        raise ValueError("Invalid scheduler endpoints")
    if min(args.epochs,args.batch_size,args.validation_batch_size,args.prefetch,args.log_every,
           args.validation_every,args.checkpoint_every,args.mixer_layers,args.mixer_hidden) < 1:
        raise ValueError("Positive run dimensions required")
    if not 0 <= args.workers <= 32 or not 0 <= args.validation_workers <= 32 or args.prefetch > 8 or args.max_updates < 0:
        raise ValueError("Unbounded loader or update configuration")
    if args.device != "cuda":
        raise ValueError("Full launch requires workstation CUDA; CPU unit tests use train directly")
    if args.resume and args.resume.resolve().parent != args.output.resolve():
        raise ValueError("Resume only in the original run directory")
    args.preparation, args.shard_prefetch, args.shard_prefetch_mib = "raw_gpu", 16, 64
    print(subprocess.list2cmdline([sys.executable,"-B","-m","research.bar_gpt.phase1_v28.run_train",*sys.argv[1:]]),flush=True)
    preflight(args, desktop_paths=(r"c:\windows\system32\applicationframehost.exe",r"c:\windows\immersivecontrolpanel\systemsettings.exe"))
    torch.set_num_threads(4)
    torch.manual_seed(args.seed); random.seed(args.seed); np.random.seed(args.seed)
    model, manifest, contract = build(args)
    if args.resume:
        load_checkpoint(args.resume, contract)
    configuration = dict(model=contract, output=str(args.output), resume=str(args.resume) if args.resume else None,
                         arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()})
    with lease(args.output):
        load_env_files(discover_env_files(Path(__file__).resolve().parents[3]), verbose=False)
        os.environ.setdefault("WANDB_ENTITY","mehdifaraji")
        reporter = Reporter(args.output, configuration, mode=args.wandb, project="bargpt-phase1-v28-masked-finetune32")
        write_json(args.output/"manifest.json",configuration)
        try:
            state = train(model,manifest,args,contract,reporter)
            reporter.finish("qualification_complete" if state == "bounded_complete" else state)
        except ReportingFailure as error:
            reporter.close_local("failed",repr(error)); raise
        except BaseException as error:
            reporter.finish("failed",repr(error)); raise


if __name__ == "__main__":
    main()
