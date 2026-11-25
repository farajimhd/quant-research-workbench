"""Verified one-pass Phase2 launch and bounded workstation profiler."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
import argparse
import json
import math
from pathlib import Path
import time
import torch
from ..phase1_v8.runtime import preflight
from ..phase1_v29.model import Autoencoder, Config
from ..phase2_v4.data import load_units
from ..phase2_v2.train import lease, load_checkpoint, save_checkpoint, rng_state, restore_rng
from ..phase1_v26.reporting import Reporter
from ..v5.shards.contract import file_hash, write_json
from research.mlops.env import discover_env_files, load_env_files
from .data import sessions, DeviceSession
from .metrics import Metrics
from .model import Forecast, HORIZONS, NAMES, objective


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--source-revision", required=True)
    p.add_argument("--release", required=True, type=Path)
    p.add_argument("--teacher", required=True, type=Path)
    p.add_argument("--calibration", required=True, type=Path)
    p.add_argument("--resume", type=Path)
    p.add_argument("--batch-size", type=int, default=4096)
    p.add_argument("--token-batch", type=int, default=1024)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--profile-sessions", type=int, default=0)
    p.add_argument("--validation-every", type=int, default=10000)
    p.add_argument("--checkpoint-every", type=int, default=500)
    p.add_argument("--eval-sessions", type=int, default=32)
    p.add_argument("--learning-rate", type=float, default=1e-4)
    p.add_argument("--head-bf16", action="store_true")
    return p


def teacher_for(args):
    saved = torch.load(args.teacher, map_location="cpu", weights_only=False)
    saved = load_checkpoint(args.teacher, saved["contract"])
    contract = saved["contract"]
    if contract["version"] != "phase1-v29-masked-finetune-structural-v1":
        raise ValueError("Phase2 requires the verified V29 checkpoint")
    if file_hash(args.calibration) != contract["calibration_sha256"]:
        raise ValueError("V29 calibration mismatch")
    calibration = json.loads(args.calibration.read_text())["parameters"]
    teacher = Autoencoder(calibration, Config(**contract["config"]))
    teacher.load_state_dict({**saved["model"], "_extra_state": saved["model_extra"]})
    teacher.requires_grad_(False).eval().cuda()
    return teacher, {"step": saved["step"], "sha256": file_hash(args.teacher),
                     "calibration_sha256": contract["calibration_sha256"]}


def predict(model, teacher, windows, args):
    with torch.autocast("cuda", dtype=torch.bfloat16, enabled=args.head_bf16):
        encoded = model(windows)
    return teacher.decode(encoded.float())


@torch.no_grad()
def evaluate(units, model, teacher, args, limit):
    stats = Metrics("cuda")
    count, total_loss = 0, 0.
    batches = sessions(units, 1, 0, args.workers)
    started = time.perf_counter()
    try:
        for number, (_, source) in enumerate(batches):
            if not len(source.origins):
                continue
            device = DeviceSession(source, teacher, args.token_batch, "cuda")
            for ids in torch.arange(len(source.origins), device="cuda").split(args.batch_size):
                inputs, logs, valid, evidence = device.batch(ids, metrics=True)
                out = predict(model, teacher, inputs, args)
                total_loss += float(objective(teacher, out, logs, valid))*len(ids)
                stats.add(out, valid, evidence)
                count += len(ids)
            if limit and number+1 >= limit:
                break
    finally:
        batches.close()
    if count == 0:
        raise ValueError("Validation has no eligible targets")
    result = stats.result()
    if result["nonfinite"]:
        raise FloatingPointError("Nonfinite forecast during validation")
    return {**result, "loss": total_loss/count, "origins": count,
            "seconds": time.perf_counter()-started, "full_split": not limit}


def train(args, units, teacher, model, optimizer, contract, reporter=None, saved=None):
    step = saved["step"] if saved else 0
    cursor = tuple(saved["cursor"]) if saved else (0, 0)
    count = saved["windows"] if saved else 0
    next_validation = ((step//args.validation_every)+1)*args.validation_every
    best = saved.get("best", float("inf")) if saved else float("inf")
    if saved:
        model.load_state_dict(saved["model"])
        optimizer.load_state_dict(saved["optimizer"])
        restore_rng(saved["rng"])
    def checkpoint(name="checkpoint_last.pt"):
        save_checkpoint(args.output/name, dict(model=model.state_dict(), optimizer=optimizer.state_dict(),
                        contract=contract, rng=rng_state(), step=step, cursor=cursor,
                        windows=count, best=best))
    def stopped():
        return (args.output/"STOP").exists() or (args.output.parent/"STOP").exists()
    first, first_offset = cursor
    source = sessions(units, 0, first, args.workers)
    start = interval = time.perf_counter()
    prior_count = count
    windows_encoded = 0
    missing = [0]*5
    spans = []
    try:
        for number, (index, session) in enumerate(source):
            if stopped():
                break
            if not len(session.origins):
                cursor = (index+1, 0)
                continue
            tick = time.perf_counter()
            with torch.no_grad():
                device = DeviceSession(session, teacher, args.token_batch, "cuda")
            windows_encoded += device.num_windows
            missing = [a+b for a,b in zip(missing,device.missing)]
            generator = torch.Generator(device="cuda").manual_seed(51771+index)
            order = torch.randperm(len(session.origins), generator=generator, device="cuda")
            offset = first_offset if index == first else 0
            for ids in order[offset:].split(args.batch_size):
                if not len(ids) or stopped():
                    break
                inputs, logs, valid, _ = device.batch(ids)
                rate = args.learning_rate * min(1., (step+1)/1000)
                for group in optimizer.param_groups:
                    group["lr"] = rate
                optimizer.zero_grad(set_to_none=True)
                loss = objective(teacher, predict(model, teacher, inputs, args), logs, valid)
                loss.backward()
                norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.)
                if not bool(torch.isfinite(loss)&torch.isfinite(norm)):
                    checkpoint()
                    raise FloatingPointError(f"Invalid update {step} at {index}/{offset}")
                optimizer.step()
                step += 1
                count += len(ids)
                offset += len(ids)
                cursor = (index, offset) if offset < len(order) else (index+1, 0)
                if reporter and step % 10 == 0:
                    speed = (count-prior_count)/max(time.perf_counter()-interval, .001)
                    metrics = {"train/loss": float(loss), "train/origins_per_second": speed,
                               "train/learning_rate": rate, "train/origins": count}
                    reporter.log(metrics, step)
                    reporter.update(stage="training", step=step, epoch=1, done=count,
                                    loss=float(loss), windows_per_second=speed,
                                    learning_rate=rate, session_index=index)
                    interval, prior_count = time.perf_counter(), count
                if reporter and step % args.checkpoint_every == 0:
                    checkpoint()
                if reporter and step >= next_validation:
                    reporter.update(stage="development validation")
                    report = evaluate(units, model, teacher, args, args.eval_sessions)
                    write_json(args.output/"development-latest.json", dict(step=step, **report))
                    reporter.log({"development/"+k:v for k,v in report.items()}, step)
                    reporter.update(latest_evaluation={"scope":"development","step":step,"loss":report["loss"]})
                    if report["loss"] < best:
                        best = report["loss"]
                        checkpoint("checkpoint_best.pt")
                    next_validation += args.validation_every
                    interval, prior_count = time.perf_counter(), count
            spans.append(time.perf_counter()-tick)
            if args.profile_sessions and number+1 >= args.profile_sessions:
                break
        if reporter:
            checkpoint()
    finally:
        source.close()
    elapsed = time.perf_counter()-start
    profile = dict(origins=count, updates=step, seconds=elapsed, origins_per_second=count/elapsed,
                   encoded_windows=windows_encoded, missing_slots=dict(zip(NAMES,missing)),
                   session_compute_seconds=sum(spans), peak_gpu_bytes=torch.cuda.max_memory_allocated())
    if not reporter:
        return profile
    if stopped():
        return "stopped"
    reporter.update(stage="full validation")
    report = evaluate(units, model, teacher, args, 0)
    write_json(args.output/"validation-final.json", dict(step=step, **report))
    reporter.log({"validation/"+k:v for k,v in report.items()}, step)
    reporter.update(latest_evaluation={"scope":"validation","step":step,"loss":report["loss"]})
    return "complete"


def main():
    args = parser().parse_args()
    args.device = "cuda"
    if min(args.batch_size,args.token_batch,args.workers,args.validation_every,args.checkpoint_every,args.eval_sessions)<1 or args.workers>32:
        raise ValueError("Invalid or unbounded launch settings")
    if args.profile_sessions < 0 or not math.isfinite(args.learning_rate) or args.learning_rate <= 0:
        raise ValueError("Invalid profile budget or learning rate")
    if args.resume and (args.profile_sessions or args.resume.resolve().parent != args.output.resolve()):
        raise ValueError("Resume requires the same full training directory")
    preflight(args, desktop_paths=(r"c:\windows\system32\applicationframehost.exe",
                                   r"c:\windows\immersivecontrolpanel\systemsettings.exe"))
    torch.set_num_threads(1)
    torch.manual_seed(51771)
    started = time.perf_counter()
    teacher, provenance = teacher_for(args)
    units, data = load_units(release=args.release)
    model = Forecast().cuda()
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, fused=True)
    contract = dict(version="phase2-v5-direct-multiscale", source=args.source_revision,
                    teacher=provenance, data=data, horizons=HORIZONS, parameters=sum(p.numel() for p in model.parameters()),
                    label_policy="observed intervals start >= origin; verified daily successor; same split",
                    batch_size=args.batch_size, token_batch=args.token_batch, head_bf16=args.head_bf16,
                    learning_rate=args.learning_rate, warmup_updates=1000, seed=51771)
    saved = load_checkpoint(args.resume, contract) if args.resume else None
    with lease(args.output):
        if args.profile_sessions:
            profile = train(args, units, teacher, model, optimizer, contract)
            profile["setup_seconds"] = time.perf_counter()-started-profile["seconds"]
            validation = evaluate(units, model, teacher, args, min(args.eval_sessions, 4))
            write_json(args.output/"profile.json", {**profile, "validation_origins": validation["origins"],
                                                     "validation_seconds": validation["seconds"],
                                                     "validation_loss": validation["loss"]})
            print(json.dumps(profile), flush=True)
            return
        load_env_files(discover_env_files(Path.cwd()), verbose=False)
        os.environ.setdefault("WANDB_ENTITY","mehdifaraji")
        configuration = dict(model=contract, resume=str(args.resume) if args.resume else None,
                             arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()})
        reporter = Reporter(args.output, configuration, mode="online", project="bargpt-phase2")
        write_json(args.output/"manifest.json", configuration)
        try:
            state = train(args, units, teacher, model, optimizer, contract, reporter, saved)
        except BaseException as error:
            reporter.finish("failed", repr(error))
            raise
        reporter.finish(state)


if __name__ == "__main__":
    main()
