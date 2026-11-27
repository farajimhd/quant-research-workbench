"""Matched, bounded profiles at a sealed V5 cursor. Never writes to the parent run."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")

import argparse
from dataclasses import replace
import json
from pathlib import Path
import time

import torch
from ..phase2_v5 import run_train as v5
from ..phase2_v5.schedule import order_for_pass, candidate_prefix
from ..phase2_v5.unique_data import load_unique_release
from .data import BarSession
from .model import BarForecast, CachedEvaluation, CachedGradients


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--parent", required=True, type=Path)
    cli.add_argument("--output", required=True, type=Path)
    cli.add_argument("--source-revision", required=True)
    cli.add_argument("--arm", choices=("v29", "bar-cache"), required=True)
    cli.add_argument("--sessions", type=int, default=1536)
    cli.add_argument("--eval-sessions", type=int, default=256)
    cli.add_argument("--fresh", action="store_true", help="Compare equally seeded forecasts at the same traversal cursor")
    opt = cli.parse_args()
    if not (1 <= opt.sessions <= 12000 and 1 <= opt.eval_sessions <= 1024):
        raise ValueError("Expected bounded training and development session counts")
    manifest = json.loads((opt.parent/"manifest.json").read_text())
    command = json.loads((opt.parent/"process.json").read_text())["command"]
    args = v5.parser().parse_args(command[command.index("--source-revision"):])
    if args.output.resolve() != opt.parent.resolve() or not (opt.parent/"STOP").exists():
        raise ValueError("Expected the original stopped parent and its pinned command")
    if json.loads((opt.parent/"progress.json").read_text())["state"] != "stopped":
        raise ValueError("Parent did not stop cleanly")
    args.resume = None
    args.output, args.source_revision, args.device = opt.output, opt.source_revision, "cuda"
    args.profile_sessions, args.eval_sessions = opt.sessions, opt.eval_sessions
    args.passes = v5.UNIQUE_PASSES
    args.loss_weights = replace(args.loss_weights, anchored_direction=args.anchored_direction)
    contract = manifest["execution_model"]
    if vars(args.loss_weights) != contract["loss_weights"] or args.tf32 or args.head_bf16:
        raise ValueError("Loss or precision differs from the comparison contract")
    v5.preflight(args, desktop_paths=(r"c:\windows\system32\applicationframehost.exe",
                                      r"c:\windows\immersivecontrolpanel\systemsettings.exe"))
    torch.set_num_threads(1)
    torch.set_float32_matmul_precision("highest")
    torch.manual_seed(51771)
    teacher, identity = v5.teacher_for(args)
    units, release = load_unique_release(args.unique_release)
    assert identity == contract["teacher"] and release["hash"] == contract["data"]["hash"]
    units.locality = args.locality
    state = v5.load_checkpoint(opt.parent/"checkpoint_last.pt", contract)
    traversal = order_for_pass(units, state["pass_index"])
    args.pass_origins_total = candidate_prefix(units, len(units))
    assert candidate_prefix(traversal, *state["cursor"]) == state["schedule_candidates"]
    legacy = v5.Forecast().cuda()
    prior_opt = torch.optim.AdamW(legacy.parameters(), lr=args.learning_rate, fused=True)
    if not opt.fresh:
        legacy.load_state_dict(state["model"])
        prior_opt.load_state_dict(state["optimizer"])
    if opt.arm == "bar-cache":
        model = BarForecast(legacy).cuda()
        optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, fused=True)
        # Shared modules retain their actual parameter objects and AdamW moments.
        # The new residual starts with empty optimizer state.
        optimizer.state.update(prior_opt.state)
        session_type, gradients, evaluation = BarSession, CachedGradients, CachedEvaluation
    else:
        model, optimizer = legacy, prior_opt
        session_type, gradients, evaluation = v5.DeviceSession, v5.ForecastGradients, v5.ForecastEvaluation
    saved = dict(state, model=model.state_dict(), optimizer=optimizer.state_dict())
    config = dict(arm=opt.arm, fresh=opt.fresh, source=opt.source_revision, parent_step=state["step"],
                  parent_sha256=v5.file_hash(opt.parent/"checkpoint_last.pt"), parent_cursor=state["cursor"],
                  trainable_parameters=sum(p.numel() for p in model.parameters()), teacher=identity,
                  release_sha256=release["hash"], contract=contract, arguments=vars(args))
    config = json.loads(json.dumps(config, default=str))
    v5.write_json(args.output/"manifest.json", config)
    v5.load_env_files(v5.discover_env_files(Path.cwd()), verbose=False)
    import wandb
    with wandb.init(entity="mehdifaraji", project="bargpt-phase2", name=args.output.name,
                    dir=str(args.output), config=config, save_code=False,
                    settings=wandb.Settings(disable_git=True)) as run:
        v5.write_json(args.output/"wandb.json", {"url":run.url, "id":run.id})
        engine = evaluation(model, teacher, args)
        def assess(label):
            result = v5.evaluate(units, model, teacher, args, args.eval_sessions, evaluation=engine,
                                 session_type=session_type)
            if result is None:
                raise InterruptedError("Evaluation stopped")
            v5.write_json(args.output/(label+".json"), result)
            run.log({label+"/"+k:v for k,v in result.items()})
            print(label, result["loss"], result["origins_per_second"], flush=True)
        assess("before")
        profile = v5.train_pass(args, traversal, teacher, model, optimizer, contract, saved=saved,
                                session_type=session_type, gradients_type=gradients)
        if (args.output/"STOP").exists() or (args.output.parent/"STOP").exists():
            raise InterruptedError("Profile stopped")
        v5.write_json(args.output/"train.json", profile)
        run.log({"train/"+k:v for k,v in profile.items() if isinstance(v,(float,int))})
        print("train", profile["interval_origins"], profile["origins_per_second"], flush=True)
        assess("after")


if __name__ == "__main__":
    main()
