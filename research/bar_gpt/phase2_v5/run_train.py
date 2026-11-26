"""Verified one-pass Phase2 launch and bounded workstation profiler."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
import argparse
from collections import Counter
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
from .data import sessions, device_sessions
from .metrics import Metrics
from .model import Forecast, HORIZONS, NAMES, objective
from .schedule import PASSES, FLOOR, PEAK_FACTOR, LEGACY_SOURCE, cosine_rate, order_for_pass, candidate_prefix


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--source-revision", required=True)
    p.add_argument("--release", required=True, type=Path)
    p.add_argument("--teacher", required=True, type=Path)
    p.add_argument("--calibration", required=True, type=Path)
    p.add_argument("--resume", type=Path)
    p.add_argument("--resume-prepack", action="store_true", help="Resume the verified pre-packing execution contract")
    p.add_argument("--resume-pre-overlap-fix", action="store_true",
                   help="Resume the verified checkpoint from before overlap-aware origin validation")
    p.add_argument("--migrate-fixed-lr", action="store_true", help="Adopt the ten-pass schedule from the original V5 checkpoint")
    p.add_argument("--batch-size", type=int, default=4096)
    p.add_argument("--token-batch", type=int, default=1024)
    p.add_argument("--graph-updates", action="store_true", help="Replay fixed-shape forecast forward/backward on CUDA")
    p.add_argument("--session-lanes", type=int, default=0, choices=(0, 1, 2, 4),
                   help="Concurrent frozen GPU session encoders; zero uses the serial path")
    p.add_argument("--resume-serial", action="store_true", help="Resume the packed serial implementation")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--profile-sessions", type=int, default=0)
    p.add_argument("--validation-every", type=int, default=10000)
    p.add_argument("--checkpoint-every", type=int, default=500)
    p.add_argument("--eval-sessions", type=int, default=32)
    p.add_argument("--learning-rate", type=float, default=1e-4)
    p.add_argument("--head-bf16", action="store_true")
    p.add_argument("--direct-teacher", action="store_true")
    p.add_argument("--tf32", action="store_true", help="Opt into the audited fast matrix multiplication path")
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


class ForecastGradients:
    """Cache static-buffer graphs for common batch sizes, with eager handling of tails.

    Graphs contain only forward and backward. The caller checks gradients and
    applies AdamW once per original batch with its current learning rate.
    """
    def __init__(self, model, teacher, args):
        self.model, self.teacher, self.args = model, teacher, args
        self.graphs = {}
        self.warmed = set()

    def backward(self, inputs, logs, valid):
        loss = objective(self.teacher, predict(self.model, self.teacher, inputs, self.args), logs, valid)
        loss.backward()
        # Release AccumulateGrad nodes before recording on a different stream.
        return loss.detach()

    def __call__(self, inputs, logs, valid):
        n = len(inputs)
        if not self.args.graph_updates or n not in (4096, 8192):
            return self.backward(inputs, logs, valid)
        if n not in self.warmed:
            self.warmed.add(n)
            return self.backward(inputs, logs, valid)
        arguments = (inputs, logs, valid)
        if n not in self.graphs:
            buffers = tuple(torch.empty_like(x) for x in arguments)
            graph = torch.cuda.CUDAGraph()
            # Capture uses dedicated buffers and fresh gradient storage. No
            # parameter update or RNG use occurs during recording.
            with torch.cuda.graph(graph, capture_error_mode="thread_local"):
                loss = self.backward(*buffers)
            grads = tuple(p.grad for p in self.model.parameters())
            self.graphs[n] = graph, buffers, loss, grads
        graph, buffers, loss, grads = self.graphs[n]
        for dst, src in zip(buffers, arguments):
            dst.copy_(src)
        for parameter, grad in zip(self.model.parameters(), grads):
            parameter.grad = grad
        graph.replay()
        return loss


@torch.no_grad()
def evaluate(units, model, teacher, args, limit, reporter=None):
    stats = Metrics("cuda")
    count = 0
    total_loss = torch.zeros((),device="cuda",dtype=torch.float64)
    batches = device_sessions(sessions(units, 1, 0, args.workers), teacher, args, metrics=True)
    started = time.perf_counter()
    missing = Counter()
    try:
        for number, (_, source, device) in enumerate(batches):
            if (args.output/"STOP").exists() or (args.output.parent/"STOP").exists():
                return None
            if not len(source.origins):
                continue
            missing.update(dict(zip(NAMES,device.missing)))
            for ids in torch.arange(len(source.origins), device="cuda").split(args.batch_size):
                inputs, logs, valid, evidence = device.batch(ids, metrics=True)
                out = predict(model, teacher, inputs, args)
                total_loss += objective(teacher, out, logs, valid).double()*len(ids)
                stats.add(out, valid, evidence)
                count += len(ids)
            if reporter and number % 8 == 0:
                elapsed = time.perf_counter()-started
                reporter.update(validation_done=count, validation_sessions=number+1,
                                validation_samples_per_second=count/max(elapsed,.001),
                                validation_windows_per_second=count/max(elapsed,.001))
            if limit and number+1 >= limit:
                break
    finally:
        batches.close()
    if count == 0:
        raise ValueError("Validation has no eligible targets")
    result = stats.result()
    if result["nonfinite"]:
        raise FloatingPointError("Nonfinite forecast during validation")
    elapsed = time.perf_counter()-started
    return {**result, **{f"missing/{frame}":missing[frame] for frame in NAMES},
            "loss": float(total_loss)/count, "origins": count, "origins_per_second":count/elapsed,
            "seconds": time.perf_counter()-started, "full_split": not limit}


def train_pass(args, units, teacher, model, optimizer, contract, reporter=None, saved=None):
    step = saved["step"] if saved else 0
    cursor = tuple(saved["cursor"]) if saved else (0, 0)
    count = saved["windows"] if saved else 0
    pass_index = saved.get("pass_index", 0) if saved else 0
    pass_origins = saved.get("pass_origins", 0) if saved else 0
    candidates = candidate_prefix(units, *cursor)
    pass_total = args.pass_origins_total
    next_validation = ((step//args.validation_every)+1)*args.validation_every
    best = saved.get("best", float("inf")) if saved else float("inf")
    if saved:
        model.load_state_dict(saved["model"])
        optimizer.load_state_dict(saved["optimizer"])
        restore_rng(saved["rng"])
    def checkpoint(name="checkpoint_last.pt"):
        save_checkpoint(args.output/name, dict(model=model.state_dict(), optimizer=optimizer.state_dict(),
                        contract=contract, rng=rng_state(), step=step, cursor=cursor,
                        windows=count, best=best, pass_index=pass_index,
                        pass_origins=pass_origins, schedule_candidates=candidates))
    def stopped():
        return (args.output/"STOP").exists() or (args.output.parent/"STOP").exists()
    first, first_offset = cursor
    source = device_sessions(sessions(units, 0, first, args.workers), teacher, args, metrics=False)
    start = interval = time.perf_counter()
    gradients = ForecastGradients(model, teacher, args)
    starting_count = prior_count = count
    windows_encoded = 0
    missing = [0]*5
    daily_status = Counter()
    rejected = 0
    compute_seconds = 0.
    first_loss = last_loss = None
    try:
        for number, (index, session, device) in enumerate(source):
            if stopped():
                break
            rejected += session.rejected_origins
            daily_status[session.daily_status] += 1
            if not len(session.origins):
                candidates += session.rejected_origins
                cursor = (index+1, 0)
                continue
            tick = time.perf_counter()
            windows_encoded += device.num_windows
            missing = [a+b for a,b in zip(missing,device.missing)]
            generator = torch.Generator(device="cuda").manual_seed(51771 + index + 1_000_003 * pass_index)
            order = torch.randperm(len(session.origins), generator=generator, device="cuda")
            offset = first_offset if index == first else 0
            for ids in order[offset:].split(args.batch_size):
                if not len(ids) or stopped():
                    break
                inputs, logs, valid, _ = device.batch(ids)
                last_batch = offset + len(ids) == len(order)
                traversed = len(ids) + (session.rejected_origins if last_batch else 0)
                rate = cosine_rate(args.learning_rate, pass_index, candidates + traversed, pass_total)
                for group in optimizer.param_groups:
                    group["lr"] = rate
                optimizer.zero_grad(set_to_none=True)
                loss = gradients(inputs, logs, valid)
                norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.)
                if not bool(torch.isfinite(loss)&torch.isfinite(norm)):
                    checkpoint()
                    raise FloatingPointError(f"Invalid update {step} at {index}/{offset}")
                optimizer.step()
                step += 1
                count += len(ids)
                pass_origins += len(ids)
                candidates += traversed
                offset += len(ids)
                cursor = (index, offset) if offset < len(order) else (index+1, 0)
                if reporter and step % 10 == 0:
                    speed = (count-prior_count)/max(time.perf_counter()-interval, .001)
                    metrics = {"train/loss": float(loss), "train/origins_per_second": speed,
                               "train/learning_rate": rate, "train/origins": count}
                    metrics.update({"train/pass": pass_index+1,
                                    "train/pass_fraction": candidates/pass_total})
                    reporter.log(metrics, step)
                    reporter.update(stage="training", step=step, epoch=pass_index+1, epochs=PASSES, done=count,
                                    pass_origins_done=candidates, pass_origins_total=pass_total,
                                    samples_per_second=speed, pass_fraction=candidates/pass_total,
                                    loss=float(loss), windows_per_second=speed,
                                    learning_rate=rate, session_index=index)
                    interval, prior_count = time.perf_counter(), count
                if reporter and step % args.checkpoint_every == 0:
                    checkpoint()
                if reporter and step >= next_validation:
                    reporter.update(stage="development validation")
                    report = evaluate(units, model, teacher, args, args.eval_sessions, reporter)
                    if report is None:
                        checkpoint()
                        return "stopped"
                    write_json(args.output/"development-latest.json", dict(step=step, **report))
                    reporter.log({"development/"+k:v for k,v in report.items()}, step)
                    reporter.update(latest_evaluation={"scope":"development","step":step,"loss":report["loss"]})
                    if report["loss"] < best:
                        best = report["loss"]
                        checkpoint("checkpoint_best.pt")
                    next_validation += args.validation_every
                    interval, prior_count = time.perf_counter(), count
            compute_seconds += time.perf_counter()-tick
            if args.profile_sessions and number+1 >= args.profile_sessions:
                break
        if reporter:
            checkpoint()
    finally:
        source.close()
    elapsed = time.perf_counter()-start
    profile = dict(origins=count, updates=step, seconds=elapsed, interval_origins=count-starting_count,
                   origins_per_second=(count-starting_count)/elapsed,
                   encoded_windows=windows_encoded, missing_slots=dict(zip(NAMES,missing)),
                   rejected_origins=rejected, daily_status=dict(daily_status), consumer_update_seconds=compute_seconds,
                   first_loss=first_loss, last_loss=float(last_loss) if last_loss is not None else None, peak_gpu_bytes=torch.cuda.max_memory_allocated())
    if not reporter:
        return profile
    write_json(args.output/"training-coverage.json", profile)
    if stopped():
        return "stopped"
    if candidates != pass_total:
        raise ValueError(f"Pass coverage changed: accounted {candidates}, expected {pass_total}")
    reporter.update(stage="full validation", epoch=pass_index+1, epochs=PASSES,
                    pass_fraction=1., pass_origins_done=candidates, pass_origins_total=pass_total)
    report = evaluate(units, model, teacher, args, 0, reporter)
    if report is None:
        return "stopped"
    result = dict(step=step, data_pass=pass_index+1, **report)
    write_json(args.output/f"validation-pass-{pass_index+1:02d}.json", result)
    write_json(args.output/"validation-final.json", result)
    reporter.log({"validation/"+k:v for k,v in result.items()}, step)
    reporter.update(latest_evaluation={"scope":"validation","step":step,"loss":report["loss"]})
    pass_index += 1
    cursor, pass_origins, candidates = (0, 0), 0, 0
    checkpoint()
    return "complete" if pass_index == PASSES else "next_pass"


def train(args, units, teacher, model, optimizer, contract, reporter=None, saved=None):
    pass_index = saved.get("pass_index", 0) if saved else 0
    while pass_index < PASSES:
        active = order_for_pass(units, pass_index)
        result = train_pass(args, active, teacher, model, optimizer, contract, reporter, saved)
        if result != "next_pass":
            return result
        saved = load_checkpoint(args.output/"checkpoint_last.pt", contract)
        pass_index = saved["pass_index"]
    return "complete"


def main():
    args = parser().parse_args()
    args.device = "cuda"
    if min(args.batch_size,args.token_batch,args.workers,args.validation_every,args.checkpoint_every,args.eval_sessions)<1 or args.workers>32:
        raise ValueError("Invalid or unbounded launch settings")
    if args.profile_sessions < 0 or not math.isfinite(args.learning_rate) or args.learning_rate <= 0:
        raise ValueError("Invalid profile budget or learning rate")
    migrations = (args.migrate_fixed_lr, args.resume_prepack, args.resume_serial, args.resume_pre_overlap_fix)
    if sum(migrations) > 1 or (any(migrations) and not args.resume):
        raise ValueError("Specify at most one migration and its recovery checkpoint")
    cosine_rate(args.learning_rate, PASSES-1, 0, 1)
    if args.resume and (args.profile_sessions or args.resume.resolve().parent != args.output.resolve()):
        raise ValueError("Resume requires the same full training directory")
    preflight(args, desktop_paths=(r"c:\windows\system32\applicationframehost.exe",
                                   r"c:\windows\immersivecontrolpanel\systemsettings.exe"))
    torch.set_num_threads(1)
    torch.manual_seed(51771)
    torch.set_float32_matmul_precision("high" if args.tf32 else "highest")
    started = time.perf_counter()
    teacher, provenance = teacher_for(args)
    units, data = load_units(release=args.release)
    args.pass_origins_total = candidate_prefix(units, len(units))
    model = Forecast().cuda()
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, fused=True)
    contract = dict(version="phase2-v5-direct-multiscale", source=args.source_revision,
                    teacher=provenance, data=data, horizons=HORIZONS, parameters=sum(p.numel() for p in model.parameters()),
                    label_policy="observed intervals start >= origin; verified daily successor; same split",
                    batch_size=args.batch_size, token_batch=args.token_batch, head_bf16=args.head_bf16,
                    cached_teacher=not args.direct_teacher, tf32=args.tf32,
                    learning_rate=args.learning_rate, warmup_updates=1000, seed=51771)
    # The old scientific identity remains exact for W&B. The checkpoint also
    # binds the new execution source and schedule to all future resumes.
    legacy_contract = dict(contract, source=LEGACY_SOURCE)
    contract["schedule"] = dict(kind="cosine_by_traversed_candidates", passes=PASSES,
                                peak_factor=PEAK_FACTOR, floor=FLOOR,
                                candidates_per_pass=args.pass_origins_total,
                                pass_order_seed_stride=1009, origin_seed_stride=1_000_003)
    saved = None
    if args.resume:
        previous = dict(contract, source="1a76327f0a9cfc08beac8a88071ccf56419daafa") if args.resume_prepack else contract
        if args.resume_serial:
            previous = dict(contract, source="acefd32563d52046b4a36f992f037289b1f290a5")
        if args.resume_pre_overlap_fix:
            previous = dict(contract, source="ad834e86029ae15c2028240b24e434bb5105260a")
        saved = load_checkpoint(args.resume, legacy_contract if args.migrate_fixed_lr else previous)
        if args.migrate_fixed_lr:
            saved.update(pass_index=0,pass_origins=saved["windows"])
        else:
            active = order_for_pass(units, saved["pass_index"])
            if saved["schedule_candidates"] != candidate_prefix(active, *saved["cursor"]):
                raise ValueError("Checkpoint scheduler count disagrees with source traversal")
    with lease(args.output):
        if args.profile_sessions:
            profile = train(args, units, teacher, model, optimizer, contract)
            profile["setup_seconds"] = time.perf_counter()-started-profile["seconds"]
            validation = evaluate(units, model, teacher, args, args.eval_sessions)
            write_json(args.output/"profile.json", {**profile, "validation_origins": validation["origins"],
                                                     "validation_seconds": validation["seconds"],
                                                     "validation_origins_per_second": validation["origins_per_second"],
                                                     "validation_loss": validation["loss"]})
            print(json.dumps(profile), flush=True)
            return
        load_env_files(discover_env_files(Path.cwd()), verbose=False)
        os.environ.setdefault("WANDB_ENTITY","mehdifaraji")
        configuration = dict(model=legacy_contract, execution_model=contract,
                             resume=str(args.resume) if args.resume else None,
                             arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()})
        reporter = Reporter(args.output, configuration, mode="online", project="bargpt-phase2")
        write_json(args.output/"manifest.json", configuration)
        reporter.update(epoch=saved["pass_index"]+1 if saved else 1, epochs=PASSES)
        try:
            state = train(args, units, teacher, model, optimizer, contract, reporter, saved)
        except BaseException as error:
            reporter.finish("failed", repr(error))
            raise
        reporter.finish(state)


if __name__ == "__main__":
    main()
