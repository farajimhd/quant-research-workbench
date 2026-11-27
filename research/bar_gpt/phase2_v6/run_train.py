"""Launch or resume twenty complete Phase 2 passes using the qualified physical-bar cache."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")

import argparse
import json
import math
from pathlib import Path

import torch
from ..phase2_v5 import run_train as v5
from .data import BarSession
from .model import BarForecast, CachedGradients, CachedEvaluation


def read(path):
    return json.loads(path.read_text())


def replay(args, trial_root, trial, parent, teacher, units, legacy, prior_optimizer, model, optimizer):
    old = v5.load_checkpoint(parent / "checkpoint_last.pt", trial["contract"])
    if (v5.file_hash(parent / "checkpoint_last.pt") != trial["parent_sha256"]
            or old["step"] != trial["parent_step"] or list(old["cursor"]) != trial["parent_cursor"]):
        raise ValueError("Stopped parent differs from the profiled transfer boundary")
    legacy.load_state_dict(old["model"])
    prior_optimizer.load_state_dict(old["optimizer"])
    optimizer.state.update(prior_optimizer.state)
    saved = dict(old, model=model.state_dict(), optimizer=optimizer.state_dict())
    traversal = v5.order_for_pass(units, old["pass_index"])
    reference = read(trial_root / "train.json")
    profile = v5.train_pass(args, traversal, teacher, model, optimizer, trial["contract"], saved=saved,
                            session_type=BarSession, gradients_type=CachedGradients)
    for key in ("updates", "interval_origins", "missing_slots", "rejected_origins"):
        if profile[key] != reference[key]:
            raise ValueError(f"Transfer replay coverage differs: {key}")
    report = v5.evaluate(units, model, teacher, args, args.eval_sessions,
                         evaluation=CachedEvaluation(model, teacher, args), session_type=BarSession)
    expected = read(trial_root / "after.json")
    if report is None or report.keys() != expected.keys():
        raise ValueError("Incomplete transfer replay evaluation")
    for key in expected.keys() - {"seconds", "origins_per_second"}:
        if not math.isclose(report[key], expected[key], rel_tol=1e-7, abs_tol=1e-9):
            raise ValueError(f"Transfer replay differs on {key}: {report[key]} vs {expected[key]}")
    v5.write_json(args.output / "transfer-replay.json", dict(training=profile, evaluation=report))


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", required=True, type=Path)
    cli.add_argument("--source-revision", required=True)
    mode = cli.add_mutually_exclusive_group(required=True)
    mode.add_argument("--transfer-profile", type=Path)
    mode.add_argument("--resume", action="store_true")
    opt = cli.parse_args()
    # On subsequent launches use the pinned run manifest and local V6 checkpoint.
    # The earlier V5 checkpoint and short profile are only needed at initialization.
    if opt.resume:
        config = read(opt.output / "manifest.json")
        spec = config["initialization_profile"]
    else:
        spec = read(opt.transfer_profile / "manifest.json")
    if spec["arm"] != "bar-cache" or spec["fresh"] or spec["contract"]["schedule"]["passes"] != 20:
        raise ValueError("Expected the qualified twenty-pass V6 transfer configuration")
    args = argparse.Namespace(**spec["arguments"])
    for key in ("teacher", "calibration", "unique_release"):
        setattr(args, key, Path(getattr(args, key)))
    args.loss_weights = v5.LossWeights(**spec["contract"]["loss_weights"])
    args.source_revision, args.output = opt.source_revision, opt.output
    args.resume = opt.output / "checkpoint_last.pt" if opt.resume else None
    v5.preflight(args, desktop_paths=(r"c:\windows\system32\applicationframehost.exe",
                                      r"c:\windows\immersivecontrolpanel\systemsettings.exe"))
    with v5.lease(args.output):
        torch.set_num_threads(1)
        torch.set_float32_matmul_precision("highest")
        torch.manual_seed(51771)
        teacher, identity = v5.teacher_for(args)
        units, release = v5.load_unique_release(args.unique_release)
        units.locality = args.locality
        if (identity != spec["teacher"] or release["hash"] != spec["release_sha256"] or
                v5.candidate_prefix(units, len(units)) != args.pass_origins_total):
            raise ValueError("Frozen teacher or unique data no longer matches the profile")
        legacy = v5.Forecast().cuda()
        prior_optimizer = torch.optim.AdamW(legacy.parameters(), lr=args.learning_rate, fused=True)
        model = BarForecast(legacy).cuda()
        optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, fused=True)
        contract = dict(spec["contract"], version="phase2-v6-bar-cache-full20", source=args.source_revision,
                        parameters=sum(p.numel() for p in model.parameters()), parent_sha256=spec["parent_sha256"],
                        transfer_profile_source=spec["source"], transfer_sessions=args.profile_sessions)
        checkpoint = args.output / "checkpoint_last.pt"
        if opt.resume:
            saved = v5.load_checkpoint(checkpoint, contract)
            active = v5.order_for_pass(units, saved["pass_index"])
            if saved["schedule_candidates"] != v5.candidate_prefix(active, *saved["cursor"]):
                raise ValueError("Resume cursor differs from the scheduler")
            if saved["pass_index"] >= args.passes:
                raise ValueError("All requested passes already completed")
        else:
            # The qualified profile references the parent's stopped output via its original command.
            parent_command = read(opt.transfer_profile / "process.json")["command"]
            parent = Path(parent_command[parent_command.index("--parent") + 1])
            replay(args, opt.transfer_profile, spec, parent, teacher, units, legacy,
                   prior_optimizer, model, optimizer)
            saved = dict(model=model.state_dict(), optimizer=optimizer.state_dict(), rng=v5.rng_state(),
                         contract=contract, step=0, windows=0, cursor=(0, 0), best=float("inf"),
                         pass_index=0, pass_origins=0, schedule_candidates=0)
            v5.save_checkpoint(checkpoint, saved)
        args.profile_sessions = 0
        resume_writer = opt.resume and (args.output / "wandb-identity.json").is_file()
        config = dict(model=contract, execution_model=contract, initialization_profile=spec,
                      panel_title="Phase 2 V6 · cached bars", resume=str(checkpoint) if resume_writer else None,
                      arguments=vars(args), wandb_upload_files=False)
        config = json.loads(json.dumps(config, default=str))
        v5.write_json(args.output / "manifest.json", config)
        v5.load_env_files(v5.discover_env_files(Path.cwd()), verbose=False)
        os.environ.setdefault("WANDB_ENTITY","mehdifaraji")
        reporter = v5.Reporter(args.output, config, mode="online", project="bargpt-phase2", upload_files=False)
        try:
            v5.write_json(args.output / "manifest.json", config)
            reporter.update(epoch=saved["pass_index"]+1, epochs=args.passes, step=saved["step"],
                            done=saved["windows"], pass_origins_done=saved["schedule_candidates"],
                            pass_origins_total=args.pass_origins_total)
            status = v5.train(args, units, teacher, model, optimizer, contract, reporter, saved,
                              session_type=BarSession, gradients_type=CachedGradients,
                              evaluation_type=CachedEvaluation)
        except BaseException as error:
            reporter.finish("failed", repr(error))
            raise
        reporter.finish(status)


if __name__ == "__main__":
    main()
