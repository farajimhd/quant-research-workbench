"""Readable full-population trainer with explicit checkpoint and resume boundaries.

This deliberately starts with eager FP32 execution. CUDA graphs or mixed
precision must earn their place through parity and workstation profiling; a
short model implementation is not evidence that a faster kernel is equivalent.
"""

import time
import numpy as np
import torch

from ..phase1_v11.pipeline import loader, DeviceBatches
from ..v5.data_v3 import close_loader
from ..v5.shards.phase1_contract import write_json, file_hash
from ..phase1_v7.full_data import prepare_raw
from ..phase2_v2.train import rng_state, restore_rng, save_checkpoint, load_checkpoint
from ..phase1_v26.reporting import ReportingFailure
from .data import forward_batch
from .metrics import Metrics
from .objectives import objective
from .schedule import learning_rate
from .execution import Replay, EvaluationReplay
from .eligibility import reasons


def stop_requested(args):
    """Never consume or remove a human STOP marker."""
    return (args.output / "STOP").exists() or (args.output.parent / "STOP").exists()


@torch.no_grad()
def evaluate(model, manifest, args, reporter, step, rows=None, scope=None):
    """Evaluate either fixed development rows or the ENTIRE validation split.

    An interrupted pass returns None and cannot become a best checkpoint. Completed
    coverage is checked against the manifest before publishing final metrics.
    """
    previous_mode = model.training
    model.eval()
    metric = Metrics(args.device)
    source = device = None
    evaluation_replay = None
    start = time.perf_counter()
    scope = scope or ("development" if rows is not None else "validation")
    training_sample = scope == "training_sample"
    total = len(rows) if rows is not None else manifest["counts"]["validation"]
    try:
        if rows is not None:
            # The two frozen samples are small. Cache prepared GPU inputs and
            # their graph, never predictions: replay reads current model weights.
            # Full validation continues streaming the entire certified split.
            cache = getattr(model, "_evaluation_cache", {})
            key = (scope, id(rows), args.validation_batch_size, args.device)
            cached = cache.get(key) if getattr(args, "graph_validation", False) else None
            if cached is not None:
                batches, metric, evaluation_replay = cached
                evaluation_replay.reset()
            else:
                batches = tuple(
                prepare_raw(rows[i : i + args.validation_batch_size], native=True).to(args.device)
                for i in range(0, len(rows), args.validation_batch_size)
                )
                if getattr(args, "graph_validation", False):
                    evaluation_replay = EvaluationReplay(model, batches[0], metric)
                    cache[key] = (batches, metric, evaluation_replay)
                    model._evaluation_cache = cache
        else:
            source = loader(manifest, args, "validation")
            device = DeviceBatches(source, args.device)

            def stream():
                while True:
                    try:
                        batch = device.pop()
                    except StopIteration:
                        return
                    yield batch
                    device.advance()

            batches = stream()
        for index, batch in enumerate(batches):
            if stop_requested(args):
                reporter.update(validation_complete=False)
                return None
            if (getattr(args, "graph_validation", False)
                    and len(batch.target) == args.validation_batch_size):
                if evaluation_replay is None and metric.windows == 0:
                    evaluation_replay = EvaluationReplay(model, batch, metric)
                if evaluation_replay is not None:
                    evaluation_replay(batch)
                else:
                    out, values, valid = forward_batch(model, batch)
                    metric.add(model, out, values, valid, batch)
            else:
                out, values, valid = forward_batch(model, batch)
                metric.add(model, out, values, valid, batch)
            if index % args.log_every == 0 and not training_sample:
                elapsed = time.perf_counter() - start
                speed = metric.windows / max(elapsed, 0.001)
                reporter.update(
                    stage=scope,
                    validation_complete=False,
                    validation_windows=metric.windows,
                    validation_total=total,
                    validation_windows_per_second=speed,
                    validation_eta_seconds=(total - metric.windows) / max(speed, 0.001),
                    last_progress_at=time.time(),
                )
        if args.device == "cuda":
            torch.cuda.synchronize()
        elapsed = time.perf_counter() - start
        if metric.windows != total:
            raise ValueError(f"Incomplete {scope}: {metric.windows}/{total} windows")
        result = metric.result()
        if result["nonfinite"] or not np.isfinite(result["loss"]):
            write_json(
                args.output / f"{scope}-failure.json",
                dict(
                    step=step,
                    windows=metric.windows,
                    expected_windows=total,
                    nonfinite_outputs=result["nonfinite"],
                    complete=False,
                    reason="Nonfinite prediction or objective; retain last verified checkpoint",
                ),
            )
            raise FloatingPointError("Nonfinite validation; no completed report certified")
        result.update(
            seconds=elapsed,
            full_population=rows is None,
            step=step,
            windows_per_second=total / max(elapsed, 0.001),
        )
        write_json(args.output / f"{scope}-latest.json", result)
        summary = dict(scope=scope, step=step, windows=total, loss=result["loss"],
                       eligible_bars=result.get("eligible_bars"), excluded_bars=result.get("excluded_bars"))
        if scope in ("development", "validation"):
            reporter.update(**{"latest_development_evaluation" if scope == "development" else "latest_full_evaluation": summary})
        reporter.log({f"{scope}/{key}": value for key, value in result["metrics"].items()}, step)
        reporter.log(
            {
                f"{scope}/seconds": elapsed,
                f"{scope}/windows_per_second": result["windows_per_second"],
            },
            step,
        )
        if training_sample:
            reporter.update(latest_training_evaluation=dict(scope=scope, step=step,
                            loss=result["loss"], windows=total, seconds=elapsed))
            return result
        reporter.update(
            validation_complete=True,
            validation_windows=total,
            validation_total=total,
            validation_windows_per_second=result["windows_per_second"],
            validation_eta_seconds=0,
            latest_evaluation=dict(
                scope=scope,
                step=step,
                loss=result["loss"],
                close_mae=result["metrics"].get("raw_mae/1s/trade_close"),
                candle_direction=result["metrics"].get("candle/1s/trade/moving_direction_pct"),
                activity_direction=result["metrics"].get(
                    "activity_direction/1s/trade_volume/balanced_accuracy_pct"
                ),
            ),
        )
        return result
    finally:
        # Always release prefetch threads/workers, including on STOP or exceptions.
        if device is not None:
            device.close()
        if source is not None:
            close_loader(source)
        model.train(previous_mode)


def training_panel_sample(args, development):
    """Freeze train-only rows with the same timeframe counts as development.

    Even spacing within each timeframe avoids taking only the beginning of the
    frozen population. These are diagnostic reconstruction metrics, never inputs
    to model selection or gradient updates. Persist exact source row identities.
    """
    path = args.data / "train.npy"
    rows = np.load(path, mmap_mode="r")
    indices = []
    for frame, count in zip(*np.unique(development["timeframe_us"], return_counts=True)):
        candidates = np.flatnonzero(rows["timeframe_us"] == frame)
        if len(candidates) < count:
            raise ValueError("Not enough training rows for matched timeframe diagnostics")
        indices.extend(candidates[np.linspace(0, len(candidates)-1, int(count), dtype=np.int64)].tolist())
    indices = np.asarray(sorted(indices), dtype=np.int64)
    receipt = dict(source=str(path), source_sha256=file_hash(path), indices=indices.tolist(),
                   windows=len(indices), selection="evenly spaced per timeframe; development-matched counts")
    receipt_path = args.output / "training-panel-sample.json"
    if receipt_path.exists():
        import json
        if json.loads(receipt_path.read_text()) != receipt:
            raise ValueError("Frozen training diagnostic sample changed")
    else:
        write_json(receipt_path, receipt)
    return rows[indices]


def train(model, manifest, args, contract, reporter):
    """Train all certified windows; maintain last/best checkpoints, not archives.

    The cursor advances only AFTER an optimizer update. Replay skips consumed
    ordered batches on resume, then restores RNG so loader startup cannot perturb
    model randomness. Validation restarts if interrupted; it is never partially
    reported as complete. Engineering resumes require an identical contract.
    """
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.learning_rate, weight_decay=0.0, fused=args.device == "cuda"
    )
    step = epoch = cursor = epoch_windows = windows = 0
    phase = "train"
    best = float("inf")
    replay = None
    eligibility_totals = torch.zeros(4, device=args.device, dtype=torch.int64)
    total_windows = args.epochs * manifest["counts"]["train"]
    reporter.update(
        epochs=args.epochs, epoch_total=manifest["counts"]["train"], session_started=time.time()
    )
    if args.resume:
        saved = load_checkpoint(args.resume, getattr(args, "resume_contract", contract))
        model.load_state_dict({**saved["model"], "_extra_state": saved["model_extra"]})
        optimizer.load_state_dict(saved["optimizer"])
        step, epoch, cursor = saved["step"], saved["epoch"], saved["cursor"][1]
        windows, epoch_windows = saved["windows"], saved["epoch_windows"]
        phase, best = saved["phase"], saved["best"]
        restore_rng(saved["rng"])
        eligibility_totals.copy_(torch.tensor(saved.get("eligibility_totals", [0, 0, 0, 0]), device=args.device))

    def checkpoint(name="checkpoint_last.pt"):
        # The shared writer checks tensor finiteness. Keep semantic extra-state
        # separately so that dictionary is not mistaken for a parameter tensor.
        state = dict(model.state_dict())
        extra = state.pop("_extra_state")
        save_checkpoint(
            args.output / name,
            dict(
                contract=contract,
                model=state,
                model_extra=extra,
                optimizer=optimizer.state_dict(),
                rng=rng_state(),
                step=step,
                epoch=epoch,
                cursor=(epoch, cursor),
                epoch_windows=epoch_windows,
                windows=windows,
                phase=phase,
                best=best,
                eligibility_totals=eligibility_totals.cpu().tolist(),
                scheduler=dict(
                    kind="cosine_by_windows",
                    completed=windows,
                    total=total_windows,
                    peak=args.learning_rate,
                    floor=args.min_learning_rate,
                ),
            ),
        )
        progress = dict(checkpoint_step=step, step=step)
        if epoch < args.epochs:
            progress.update(epoch=epoch + 1, epoch_done=epoch_windows)
        reporter.update(**progress)

    development = np.load(args.data / "development.npy", mmap_mode="r")
    panel_sample = training_panel_sample(args, development) if getattr(args, "training_panel_metrics", False) else None
    checkpoint()  # A verified recovery point exists before lengthy initialization.
    if not args.resume:
        # Keep one bounded initialization receipt so from-scratch experiments can
        # prove they began with identical random weights, not a trained parent.
        checkpoint("checkpoint_initial.pt")
    try:
        if panel_sample is not None:
            if evaluate(model, manifest, args, reporter, step, panel_sample, "training_sample") is None:
                return "stopped"
            if args.resume:
                initial = evaluate(model, manifest, args, reporter, step, development)
                if initial is None:
                    return "stopped"
                if getattr(args, "precision_fork", False):
                    best = initial["loss"]
                    checkpoint("checkpoint_best.pt")
                    checkpoint()
        if not args.resume:
            initial = evaluate(model, manifest, args, reporter, step, development)
            if initial is None:
                return "stopped"
            # Keep the starting model as best until actual development evidence
            # improves it. A worse first validation must not replace the baseline.
            best = initial["loss"]
            write_json(args.output / "development-initial.json", initial)
            checkpoint("checkpoint_best.pt")
            checkpoint()
        while epoch < args.epochs:
            if stop_requested(args):
                checkpoint()
                return "stopped"
            if phase == "train":
                reporter.update(epoch=epoch + 1, epoch_done=epoch_windows, step=step)
                source = loader(manifest, args, "train", epoch)
                device = None
                try:
                    restore_point = rng_state()
                    reporter.update(
                        stage="restoring traversal" if cursor else "loading training data"
                    )
                    started = time.perf_counter()
                    iterator = iter(source)
                    for _ in range(cursor):
                        if stop_requested(args):
                            checkpoint()
                            return "stopped"
                        next(iterator)
                    device = DeviceBatches(iterator, args.device)
                    restore_rng(restore_point)
                    reporter.update(loader_startup_seconds=time.perf_counter() - started)
                    measured_at, measured_windows = time.perf_counter(), windows
                    model.train()
                    while True:
                        if stop_requested(args):
                            checkpoint()
                            return "stopped"
                        try:
                            batch = device.pop()
                        except StopIteration:
                            break
                        batch_windows = len(batch.target)
                        rate = learning_rate(
                            windows + batch_windows,
                            total_windows,
                            args.learning_rate,
                            args.min_learning_rate,
                        )
                        for group in optimizer.param_groups:
                            group["lr"] = rate
                        optimizer.zero_grad(set_to_none=True)
                        if getattr(args, "optimized", False) and batch_windows == args.batch_size:
                            if replay is None:
                                reporter.update(stage="capturing optimized training graph")
                                replay = Replay(model, batch)
                            loss, norm, _, parts = replay(batch)
                        else:
                            # Tail batches preserve every sample through the eager
                            # path instead of padding or silently discarding rows.
                            out, values, valid = forward_batch(model, batch)
                            loss, parts = objective(model, out, values, valid)
                            loss.backward()
                            norm = torch.nn.utils.clip_grad_norm_(
                                model.parameters(), 1.0, error_if_nonfinite=True
                            )
                        # Enqueue the next transfer while the current GPU work
                        # is running, before the host waits for finite checks.
                        # No next-batch optimizer update occurs before this one.
                        device.advance()
                        if not bool(torch.isfinite(loss) & torch.isfinite(norm)):
                            raise FloatingPointError("Nonfinite update rejected")
                        optimizer.step()
                        low, high, missing = reasons(batch.grid, batch.valid)
                        eligibility_totals += torch.stack(((~(low | high | missing)).sum(), low.sum(), high.sum(), missing.sum()))
                        step += 1
                        cursor += 1
                        windows += batch_windows
                        epoch_windows += batch_windows
                        if step % args.log_every == 0:
                            elapsed = time.perf_counter() - measured_at
                            speed = (windows - measured_windows) / max(elapsed, 0.001)
                            reporter.log(
                                {
                                    "train/loss": float(loss.detach()),
                                    **{
                                        f"train/{key}_loss": float(value.detach())
                                        for key, value in parts.items()
                                    },
                                    "train/learning_rate": rate,
                                    "train/gradient_norm": float(norm),
                                    "train/windows_per_second": speed,
                                    "train/bars_per_second": 64 * speed,
                                    "train/loader_wait_seconds": device.wait_seconds,
                                    **{f"train_coverage/{name}": value for name, value in zip(
                                        ("eligible_bars", "below_floor", "above_ceiling", "no_price"), eligibility_totals.cpu().tolist())},
                                },
                                step,
                            )
                            reporter.update(
                                stage="training",
                                step=step,
                                epoch=epoch + 1,
                                epochs=args.epochs,
                                epoch_done=epoch_windows,
                                epoch_total=manifest["counts"]["train"],
                                windows_per_second=speed,
                                loss=float(loss.detach()),
                                learning_rate=rate,
                                epoch_eta_seconds=(manifest["counts"]["train"] - epoch_windows)
                                / max(speed, 0.001),
                                loader_wait_seconds=device.wait_seconds,
                                last_progress_at=time.time(),
                            )
                            measured_at, measured_windows = time.perf_counter(), windows
                        if step % args.checkpoint_every == 0:
                            checkpoint()
                        if step % args.validation_every == 0:
                            if panel_sample is not None:
                                if evaluate(model, manifest, args, reporter, step, panel_sample, "training_sample") is None:
                                    checkpoint()
                                    return "stopped"
                            result = evaluate(model, manifest, args, reporter, step, development)
                            if result is None:
                                checkpoint()
                                return "stopped"
                            if result["loss"] < best:
                                best = result["loss"]
                                checkpoint("checkpoint_best.pt")
                            measured_at, measured_windows = time.perf_counter(), windows
                        if args.max_updates and step >= args.max_updates:
                            checkpoint()
                            return "bounded_complete"
                    if epoch_windows != manifest["counts"]["train"]:
                        raise ValueError("Training epoch did not cover its manifest population")
                    reporter.update(epoch_done=epoch_windows, epoch_eta_seconds=0, step=step)
                    phase = "validation"
                    checkpoint()
                finally:
                    if device is not None:
                        device.close()
                    close_loader(source)
            result = evaluate(model, manifest, args, reporter, step)
            if result is None:
                checkpoint()
                return "stopped"
            write_json(args.output / f"validation-{epoch + 1:03}.json", result)
            epoch += 1
            cursor = epoch_windows = 0
            phase = "train"
            checkpoint()
        if windows != total_windows:
            raise ValueError("Final training coverage mismatch")
        return "complete"
    except ReportingFailure:
        # Logging occurs after a committed update or a read-only evaluation, so
        # this specific failure has an unambiguous optimizer/cursor boundary.
        checkpoint()
        raise
    except KeyboardInterrupt:
        # Interrupts inside an optimizer operation are not transactional. Preserve
        # the last already VERIFIED checkpoint rather than inventing a new cursor.
        return "interrupted_use_last_checkpoint"
