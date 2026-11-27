"""Verified one-pass Phase2 launch and bounded workstation profiler."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
import argparse
from collections import Counter
from dataclasses import replace
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
from ..v5.shards.contract import digest, file_hash, write_json
from research.mlops.env import discover_env_files, load_env_files
from .data import sessions, device_sessions
from .unique_data import load_unique_release
from .metrics import Metrics
from .model import Forecast, HORIZONS, NAMES, objective
from .objectives import LossWeights, forecast_objective
from .schedule import (PASSES, FLOOR, UNIQUE_PASSES, UNIQUE_FLOOR, PEAK_FACTOR, LEGACY_SOURCE,
                       cosine_rate, order_for_pass, candidate_prefix)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--source-revision", required=True)
    release = p.add_mutually_exclusive_group(required=True)
    release.add_argument("--release", type=Path)
    release.add_argument("--unique-release", type=Path)
    p.add_argument("--teacher", required=True, type=Path)
    p.add_argument("--calibration", required=True, type=Path)
    p.add_argument("--resume", type=Path)
    p.add_argument("--from-completed-pass", type=Path,
                   help="Initialize a new unique run from the validated original pass-one boundary")
    p.add_argument("--objective-fork", type=Path, help="Start a new objective from a stopped unique-run checkpoint")
    p.add_argument("--loss-weights", type=LossWeights.parse,
                   help="price,activity,coordinate,direction,range,ohlc,from_last")
    p.add_argument("--anchored-direction", action="store_true",
                   help="Split the direction weight equally between candle/path and last-candle moves")
    p.add_argument("--locality", type=int, default=1, choices=(1, 8, 16),
                   help="Shuffle short chronological clusters and reuse exact slow-frame contexts")
    p.add_argument("--no-cross-session-cache", action="store_true",
                   help="Disable slow-frame cache while retaining the same locality order for profiling")
    p.add_argument("--resume-prepack", action="store_true", help="Resume the verified pre-packing execution contract")
    p.add_argument("--resume-pre-overlap-fix", action="store_true",
                   help="Resume the verified checkpoint from before overlap-aware origin validation")
    p.add_argument("--resume-overlap-checkpoint", action="store_true",
                   help="Resume the verified original overlap-aware training checkpoint")
    p.add_argument("--stop-after-pass", type=int, choices=range(1, UNIQUE_PASSES + 1),
                   help="Stop after saving this pass's full validation and checkpoint")
    p.add_argument("--migrate-fixed-lr", action="store_true", help="Adopt the ten-pass schedule from the original V5 checkpoint")
    p.add_argument("--batch-size", type=int, default=4096)
    p.add_argument("--token-batch", type=int, default=1024)
    p.add_argument("--graph-updates", action="store_true", help="Replay fixed-shape forecast forward/backward on CUDA")
    p.add_argument("--graph-tail-bucket", type=int, default=0, choices=(0, 1024, 2048),
                   help="Replay variable batches in bounded CUDA graph buckets using validity padding")
    p.add_argument("--graph-validation", action="store_true",
                   help="Replay the exact validation objective and metrics in CUDA graph buckets")
    p.add_argument("--resume-before-metrics-only", action="store_true",
                   help="Resume a checkpoint from the preceding reporter revision")
    p.add_argument("--session-lanes", type=int, default=0, choices=(0, 1, 2, 4),
                   help="Concurrent frozen GPU session encoders; zero uses the serial path")
    p.add_argument("--resume-serial", action="store_true", help="Resume the packed serial implementation")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--profile-sessions", type=int, default=0)
    p.add_argument("--profile-full-validation", action="store_true")
    p.add_argument("--validation-every", type=int, default=10000)
    p.add_argument("--checkpoint-every", type=int, default=500)
    p.add_argument("--eval-sessions", type=int, default=32)
    p.add_argument("--learning-rate", type=float, default=1e-4)
    p.add_argument("--head-bf16", action="store_true")
    p.add_argument("--direct-teacher", action="store_true")
    p.add_argument("--tf32", action="store_true", help="Opt into the audited fast matrix multiplication path")
    return p


def completed_pass(path, contract):
    manifest = json.loads((path.parent / "manifest.json").read_text())
    previous = manifest["execution_model"]
    saved = load_checkpoint(path, previous)
    if (previous["version"] != "phase2-v5-direct-multiscale"
            or previous["data"]["hash"] != contract["data"]["parent"]["hash"]
            or saved["pass_index"] != 1 or tuple(saved["cursor"]) != (0, 0)
            or saved["pass_origins"] != 0 or saved["schedule_candidates"] != 0):
        raise ValueError("Expected the certified source release's completed first pass")
    for key in ("teacher", "horizons", "parameters", "batch_size", "head_bf16", "cached_teacher", "tf32"):
        if previous[key] != json.loads(json.dumps(contract[key])):
            raise ValueError(f"Checkpoint initialization would change {key}")
    return saved, dict(sha256=file_hash(path), step=saved["step"], origins=saved["windows"],
                       source=previous["source"], wandb=manifest.get("wandb_identity", {}).get("id"))


def objective_fork(path, contract):
    manifest = json.loads((path.parent / "manifest.json").read_text())
    progress = json.loads((path.parent / "progress.json").read_text())
    previous = manifest["execution_model"]
    unchanged = {key: value for key, value in contract.items() if key not in ("source", "loss_weights")}
    unchanged["initialization"] = previous["initialization"]
    prior = {key: value for key, value in previous.items() if key not in ("source", "loss_weights", "objective_parent")}
    if progress["state"] != "stopped" or digest(prior) != digest(unchanged):
        raise ValueError("Objective fork requires a stopped run with matching teacher, traversal and optimizer contract")
    saved = load_checkpoint(path, previous)
    lineage = dict(sha256=file_hash(path), step=saved["step"], cursor=saved["cursor"],
                   origins=saved["windows"], source=previous["source"], wandb=manifest["wandb_identity"]["id"])
    contract.update(initialization=previous["initialization"], objective_parent=lineage)
    # The previous best used a different loss scale. Keep all optimizer and
    # traversal state while establishing a best under the new objective.
    return dict(saved, best=float("inf"))


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


def batch_objective(teacher, out, logs, valid, args, anchors=()):
    return (forecast_objective(teacher, out, logs, valid, args.loss_weights, anchors)
            if args.loss_weights else objective(teacher, out, logs, valid))


def graph_buffers(arguments, bucket):
    return tuple(x.new_zeros((bucket, *x.shape[1:])) for x in arguments)


def stage_graph_batch(buffers, arguments, n, mask_positions):
    for dst, src in zip(buffers, arguments):
        dst[:n].copy_(src)
    if n < len(buffers[0]):
        # Extra rows have no effect on per-example LayerNorm and are
        # excluded from all losses and counters by their validity masks.
        for index in mask_positions:
            buffers[index][n:].zero_()


class ForecastGradients:
    """Cache static-buffer graphs for common batch sizes, with eager handling of tails.

    Graphs contain only forward and backward. The caller checks gradients and
    applies AdamW once per original batch with its current learning rate.
    """
    def __init__(self, model, teacher, args):
        self.model, self.teacher, self.args = model, teacher, args
        self.graphs = {}
        self.warmed = set()

    def backward(self, inputs, logs, valid, *anchors):
        loss = batch_objective(self.teacher, predict(self.model, self.teacher, inputs, self.args),
                               logs, valid, self.args, anchors)
        loss.backward()
        # Release AccumulateGrad nodes before recording on a different stream.
        return loss.detach()

    def __call__(self, inputs, logs, valid, *anchors):
        n = len(inputs)
        unit = self.args.graph_tail_bucket
        bucket = min(self.args.batch_size, ((n + unit - 1) // unit) * unit) if unit and n >= 512 else n
        if not self.args.graph_updates or (n not in (4096, 8192) and not (unit and n >= 512)):
            return self.backward(inputs, logs, valid, *anchors)
        if bucket not in self.warmed:
            self.warmed.add(bucket)
            return self.backward(inputs, logs, valid, *anchors)
        arguments = (inputs, logs, valid, *anchors)
        if bucket not in self.graphs:
            buffers = graph_buffers(arguments, bucket)
            graph = torch.cuda.CUDAGraph()
            # Capture uses dedicated buffers and fresh gradient storage. No
            # parameter update or RNG use occurs during recording.
            with torch.cuda.graph(graph, capture_error_mode="thread_local"):
                loss = self.backward(*buffers)
            grads = tuple(p.grad for p in self.model.parameters())
            self.graphs[bucket] = graph, buffers, loss, grads
        graph, buffers, loss, grads = self.graphs[bucket]
        stage_graph_batch(buffers, arguments, n, (2, 4) if anchors else (2,))
        for parameter, grad in zip(self.model.parameters(), grads):
            parameter.grad = grad
        graph.replay()
        return loss


class ForecastEvaluation:
    """Keep readout, objective, and metric graph buffers across validations."""
    def __init__(self, model, teacher, args):
        self.model, self.teacher, self.args = model, teacher, args
        self.stats = Metrics("cuda")
        self.graphs = {}
        self.warmed = set()

    def compute(self, inputs, logs, valid, raw, last, last_valid, *anchors):
        out = predict(self.model, self.teacher, inputs, self.args)
        self.stats.add(out, valid, (raw, last, last_valid))
        if self.args.loss_weights:
            return forecast_objective(self.teacher, out, logs, valid, self.args.loss_weights, anchors,
                                      return_original=True)
        loss = objective(self.teacher, out, logs, valid)
        return loss, loss

    def __call__(self, inputs, logs, valid, evidence, anchors):
        n = len(inputs)
        arguments = (inputs, logs, valid, *evidence, *anchors)
        if not self.args.graph_validation or n < 512:
            return self.compute(*arguments)
        bucket = min(self.args.batch_size, ((n + 2047) // 2048) * 2048)
        if bucket not in self.warmed:
            self.warmed.add(bucket)
            return self.compute(*arguments)
        if bucket not in self.graphs:
            buffers = graph_buffers(arguments, bucket)
            graph = torch.cuda.CUDAGraph()
            with torch.cuda.graph(graph, capture_error_mode="thread_local"):
                result = self.compute(*buffers)
            self.graphs[bucket] = graph, buffers, result
        graph, buffers, result = self.graphs[bucket]
        stage_graph_batch(buffers, arguments, n, (2, 5, 7) if anchors else (2, 5))
        graph.replay()
        return result


@torch.no_grad()
def evaluate(units, model, teacher, args, limit, reporter=None, evaluation=None):
    evaluation = evaluation if evaluation is not None else ForecastEvaluation(model, teacher, args)
    evaluation.stats.reset()
    count = 0
    total_loss = torch.zeros((),device="cuda",dtype=torch.float64)
    reference_loss = torch.zeros_like(total_loss)
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
                anchors = device.forecast_anchors(ids) if args.loss_weights and args.loss_weights.uses_anchor else ()
                loss, original = evaluation(inputs, logs, valid, evidence, anchors)
                total_loss += loss.double()*len(ids)
                if args.loss_weights:
                    reference_loss += original.double()*len(ids)
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
    result = evaluation.stats.result()
    if result["nonfinite"]:
        raise FloatingPointError("Nonfinite forecast during validation")
    elapsed = time.perf_counter()-started
    if args.loss_weights:
        result["original_objective_loss"] = float(reference_loss)/count
    return {**result, **{f"missing/{frame}":missing[frame] for frame in NAMES},
            "loss": float(total_loss)/count, "origins": count, "origins_per_second":count/elapsed,
            "seconds": time.perf_counter()-started, "full_split": not limit}


def train_pass(args, units, teacher, model, optimizer, contract, reporter=None, saved=None, evaluation=None):
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
    reused_windows = 0
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
            reused_windows += device.cache_hits
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
                rate = cosine_rate(args.learning_rate, pass_index, candidates + traversed, pass_total,
                                   unique=bool(args.unique_release))
                for group in optimizer.param_groups:
                    group["lr"] = rate
                optimizer.zero_grad(set_to_none=True)
                anchors = device.forecast_anchors(ids) if args.loss_weights and args.loss_weights.uses_anchor else ()
                loss = gradients(inputs, logs, valid, *anchors)
                norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.)
                if not bool(torch.isfinite(loss)&torch.isfinite(norm)):
                    checkpoint()
                    raise FloatingPointError(f"Invalid update {step} at {index}/{offset}")
                optimizer.step()
                step += 1
                count += len(ids)
                if args.profile_sessions:
                    first_loss = loss.clone() if first_loss is None else first_loss
                    last_loss = loss
                pass_origins += len(ids)
                candidates += traversed
                offset += len(ids)
                cursor = (index, offset) if offset < len(order) else (index+1, 0)
                if reporter and step % 10 == 0:
                    speed = (count-prior_count)/max(time.perf_counter()-interval, .001)
                    metrics = {"train/loss": float(loss), "train/origins_per_second": speed,
                               "train/learning_rate": rate, "train/origins": count}
                    metrics.update({"train/pass": pass_index+1, "train/pass_fraction": candidates/pass_total,
                                    "train/distinct_context_requests": windows_encoded,
                                    "train/reused_cross_session_contexts": reused_windows,
                                    "train/context_cache_hit_fraction": 1 - (windows_encoded-reused_windows)
                                    / max(1, (count-starting_count)*len(NAMES))})
                    reporter.log(metrics, step)
                    reporter.update(stage="training", step=step, epoch=pass_index+1, epochs=args.passes, done=count,
                                    pass_origins_done=candidates, pass_origins_total=pass_total,
                                    samples_per_second=speed, pass_fraction=candidates/pass_total,
                                    loss=float(loss), windows_per_second=speed,
                                    learning_rate=rate, session_index=index)
                    interval, prior_count = time.perf_counter(), count
                if reporter and step % args.checkpoint_every == 0:
                    checkpoint()
                if reporter and step >= next_validation:
                    reporter.update(stage="development validation")
                    report = evaluate(units, model, teacher, args, args.eval_sessions, reporter, evaluation)
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
                   encoded_windows=windows_encoded-reused_windows, reused_windows=reused_windows,
                   context_cache_hit_fraction=1 - (windows_encoded-reused_windows)
                   / max(1, (count-starting_count)*len(NAMES)), missing_slots=dict(zip(NAMES,missing)),
                   rejected_origins=rejected, daily_status=dict(daily_status), consumer_update_seconds=compute_seconds,
                   first_loss=float(first_loss) if first_loss is not None else None,
                   last_loss=float(last_loss) if last_loss is not None else None,
                   peak_gpu_bytes=torch.cuda.max_memory_allocated())
    if not reporter:
        return profile
    write_json(args.output/"training-coverage.json", profile)
    if stopped():
        return "stopped"
    if candidates != pass_total:
        raise ValueError(f"Pass coverage changed: accounted {candidates}, expected {pass_total}")
    expected_eligible = getattr(units, "train_eligible", None)
    if expected_eligible is not None and pass_origins != expected_eligible:
        raise ValueError(f"Eligible training origins differ: {pass_origins} vs {expected_eligible}")
    reporter.update(stage="full validation", epoch=pass_index+1, epochs=args.passes,
                    pass_fraction=1., pass_origins_done=candidates, pass_origins_total=pass_total)
    report = evaluate(units, model, teacher, args, 0, reporter, evaluation)
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
    return "complete" if pass_index == args.passes else "next_pass"


def train(args, units, teacher, model, optimizer, contract, reporter=None, saved=None):
    pass_index = saved.get("pass_index", 0) if saved else 0
    evaluation = ForecastEvaluation(model, teacher, args) if reporter else None
    while pass_index < args.passes:
        active = order_for_pass(units, pass_index)
        result = train_pass(args, active, teacher, model, optimizer, contract, reporter, saved, evaluation)
        if result != "next_pass":
            return result
        if pass_index + 1 == getattr(args, "stop_after_pass", None):
            reporter.update(stage=f"pass {pass_index + 1} validation complete")
            return "stopped"
        saved = load_checkpoint(args.output/"checkpoint_last.pt", contract)
        pass_index = saved["pass_index"]
    return "complete"


def main():
    args = parser().parse_args()
    args.device = "cuda"
    if args.anchored_direction:
        if not args.loss_weights:
            raise ValueError("Anchored direction requires explicit loss weights")
        args.loss_weights = replace(args.loss_weights, anchored_direction=True)
    if args.graph_tail_bucket and (not args.graph_updates or args.batch_size > 8192):
        raise ValueError("Graph tail buckets require graph updates and a batch size at most 8192")
    if args.graph_validation and args.batch_size > 8192:
        raise ValueError("Graph validation requires a batch size at most 8192")
    args.passes = UNIQUE_PASSES if args.unique_release else PASSES
    if (args.from_completed_pass and (args.resume or not args.unique_release)
            or args.objective_fork and (args.resume or args.from_completed_pass or not args.unique_release)
            or args.loss_weights and not args.unique_release
            or args.profile_full_validation and not args.profile_sessions
            or args.locality > 1 and not args.unique_release
            or args.no_cross_session_cache and args.locality == 1
            or args.stop_after_pass and args.stop_after_pass > args.passes):
        raise ValueError("Invalid unique continuation or profiling options")
    if min(args.batch_size,args.token_batch,args.workers,args.validation_every,args.checkpoint_every,args.eval_sessions)<1 or args.workers>32:
        raise ValueError("Invalid or unbounded launch settings")
    if args.profile_sessions < 0 or not math.isfinite(args.learning_rate) or args.learning_rate <= 0:
        raise ValueError("Invalid profile budget or learning rate")
    migrations = (args.migrate_fixed_lr, args.resume_prepack, args.resume_serial,
                  args.resume_pre_overlap_fix, args.resume_overlap_checkpoint)
    if sum(migrations) > 1 or (any(migrations) and not args.resume):
        raise ValueError("Specify at most one migration and its recovery checkpoint")
    cosine_rate(args.learning_rate, args.passes-1, 0, 1, unique=bool(args.unique_release))
    if args.resume and (args.profile_sessions or args.resume.resolve().parent != args.output.resolve()):
        raise ValueError("Resume requires the same full training directory")
    preflight(args, desktop_paths=(r"c:\windows\system32\applicationframehost.exe",
                                   r"c:\windows\immersivecontrolpanel\systemsettings.exe"))
    torch.set_num_threads(1)
    torch.manual_seed(51771)
    torch.set_float32_matmul_precision("high" if args.tf32 else "highest")
    started = time.perf_counter()
    teacher, provenance = teacher_for(args)
    if args.unique_release:
        if any(migrations):
            raise ValueError("Legacy training cursor is incompatible with the unique population")
        units, data = load_unique_release(args.unique_release)
        if data.get("profile_only") and not args.profile_sessions:
            raise ValueError("A profiling sample cannot be used for a full training run")
        units.locality = args.locality
    else:
        units, data = load_units(release=args.release)
    args.pass_origins_total = candidate_prefix(units, len(units))
    model = Forecast().cuda()
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, fused=True)
    contract = dict(version=("phase2-v5-unique-origins" if args.unique_release else "phase2-v5-direct-multiscale"),
                    source=args.source_revision,
                    teacher=provenance, data=data, horizons=HORIZONS, parameters=sum(p.numel() for p in model.parameters()),
                    label_policy="observed intervals start >= origin; verified daily successor; same split",
                    batch_size=args.batch_size, token_batch=args.token_batch, head_bf16=args.head_bf16,
                    cached_teacher=not args.direct_teacher, tf32=args.tf32,
                    learning_rate=args.learning_rate, warmup_updates=1000, seed=51771)
    # The old scientific identity remains exact for W&B. The checkpoint also
    # binds the new execution source and schedule to all future resumes.
    legacy_contract = dict(contract, source=LEGACY_SOURCE)
    if args.loss_weights:
        weights = {key: value for key, value in vars(args.loss_weights).items()
                   if key != "anchored_direction" or value}
        contract["loss_weights"] = weights
        legacy_contract["loss_weights"] = weights
    contract["schedule"] = dict(kind="cosine_by_traversed_candidates", passes=args.passes,
                                peak_factor=PEAK_FACTOR, floor=UNIQUE_FLOOR if args.unique_release else FLOOR,
                                candidates_per_pass=args.pass_origins_total,
                                pass_order_seed_stride=1009, origin_seed_stride=1_000_003)
    saved = None
    if args.unique_release:
        contract.update(locality=args.locality, cross_session_cache=not args.no_cross_session_cache and args.locality > 1,
                        first_peak_exponent=1,
                        eligible_origins_per_pass=units.train_eligible)
    if args.from_completed_pass:
        parent, lineage = completed_pass(args.from_completed_pass, contract)
        contract["initialization"] = lineage
        saved = dict(parent, cursor=(0, 0), pass_index=0, pass_origins=0, windows=0,
                     schedule_candidates=0, best=float("inf"))
    if args.objective_fork:
        if args.objective_fork.resolve().parent == args.output.resolve():
            raise ValueError("Objective fork requires a new output directory")
        saved = objective_fork(args.objective_fork, contract)
    if args.resume:
        if args.unique_release:
            previous_manifest = json.loads((args.output/"manifest.json").read_text())
            if "initialization" in previous_manifest["execution_model"]:
                contract["initialization"] = previous_manifest["execution_model"]["initialization"]
            if "objective_parent" in previous_manifest["execution_model"]:
                contract["objective_parent"] = previous_manifest["execution_model"]["objective_parent"]
        previous = dict(contract, source="1a76327f0a9cfc08beac8a88071ccf56419daafa") if args.resume_prepack else contract
        if args.resume_before_metrics_only:
            previous = dict(contract, source="9e6c4d8d3c42404a67f97845add72f9e9a51db2e")
        if args.resume_serial:
            previous = dict(contract, source="acefd32563d52046b4a36f992f037289b1f290a5")
        if args.resume_pre_overlap_fix:
            previous = dict(contract, source="ad834e86029ae15c2028240b24e434bb5105260a")
        if args.resume_overlap_checkpoint:
            previous = dict(contract, source="fdff372b17a369cf266e1ac88f6a6b0677d72f0f")
        saved = load_checkpoint(args.resume, legacy_contract if args.migrate_fixed_lr else previous)
        if args.stop_after_pass is not None and saved.get("pass_index", 0) >= args.stop_after_pass:
            raise ValueError("Requested stop boundary was already reached by this checkpoint")
        if args.migrate_fixed_lr:
            saved.update(pass_index=0,pass_origins=saved["windows"])
        else:
            active = order_for_pass(units, saved["pass_index"])
            if saved["schedule_candidates"] != candidate_prefix(active, *saved["cursor"]):
                raise ValueError("Checkpoint scheduler count disagrees with source traversal")
    if args.objective_fork:
        active = order_for_pass(units, saved["pass_index"])
        if saved["schedule_candidates"] != candidate_prefix(active, *saved["cursor"]):
            raise ValueError("Objective fork would change the training cursor")
    with lease(args.output):
        if args.profile_sessions:
            profile = train(args, units, teacher, model, optimizer, contract, saved=saved)
            profile["setup_seconds"] = time.perf_counter()-started-profile["seconds"]
            validation = evaluate(units, model, teacher, args, 0 if args.profile_full_validation else args.eval_sessions)
            if args.profile_full_validation:
                write_json(args.output/"profile-validation.json", validation)
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
                             arguments={k:str(v) if isinstance(v,Path) else vars(v) if isinstance(v,LossWeights) else v
                                        for k,v in vars(args).items()})
        configuration["wandb_upload_files"] = False
        reporter = Reporter(args.output, configuration, mode="online", project="bargpt-phase2", upload_files=False)
        write_json(args.output/"manifest.json", configuration)
        if args.from_completed_pass or args.objective_fork:
            save_checkpoint(args.output/"checkpoint_last.pt", dict(saved, contract=contract))
        reporter.update(epoch=saved["pass_index"]+1 if saved else 1, epochs=args.passes,
                        step=saved["step"] if saved else 0, done=saved["windows"] if saved else 0,
                        pass_origins_done=saved["schedule_candidates"] if saved else 0,
                        pass_origins_total=args.pass_origins_total)
        try:
            state = train(args, units, teacher, model, optimizer, contract, reporter, saved)
        except BaseException as error:
            reporter.finish("failed", repr(error))
            raise
        reporter.finish(state)


if __name__ == "__main__":
    main()
