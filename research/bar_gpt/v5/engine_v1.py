"""Measured CUDA training path with asynchronous transfers and no per-step metric sync."""

from dataclasses import fields
import itertools
import time

import numpy as np
import torch

from .trainer_v1 import reconstruction_loss


class CudaBatches:
    """Overlap worker preparation and next-batch transfer with the current training step."""

    def __init__(self, loader):
        self.iterator = iter(loader)
        cursor = getattr(getattr(loader, "dataset", None), "replay_cursor", 0)
        if cursor:
            self.iterator = itertools.islice(self.iterator, cursor, None)
        self.stream = torch.cuda.Stream()
        self.next_batch = None
        self.wait_seconds = 0.0
        self.advance()

    def advance(self):
        """Called AFTER launching compute, so waiting on CPU data overlaps current GPU work."""
        began = time.perf_counter()
        try:
            cpu = next(self.iterator)
        except StopIteration:
            self.next_batch = None
            return
        self.wait_seconds += time.perf_counter() - began
        with torch.cuda.stream(self.stream):
            # Inputs are already pinned by DataLoader; request asynchronous copies explicitly.
            from .pretraining_v1 import PreparedBatch
            self.next_batch = PreparedBatch(**{
                field.name: getattr(cpu, field.name).to("cuda", non_blocking=True) for field in fields(cpu)
            })
            self.ready = torch.cuda.Event()
            self.ready.record(self.stream)

    def pop(self):
        """Consume only ready tensors; protect their storage until the compute stream finishes."""
        if self.next_batch is None:
            raise StopIteration
        stream = torch.cuda.current_stream()
        stream.wait_event(self.ready)
        result, self.next_batch = self.next_batch, None
        for field in fields(result):
            getattr(result, field.name).record_stream(stream)
        return result


def run_steps(model, optimizer, loader, *, steps: int, warmup: int = 0, precision="bf16",
              training=True, reporter=None, log_base=0, seconds_limit=60, stop_path=None,
              on_step=None, profiler=None, metrics_every=8, loss_every=8, before_step=None):
    """Run a bounded actual-loader window; return throughput, latency, losses, and evidence counts.

    Warmup is excluded from timing. CUDA events measure compute; wall time includes
    loader/transfer stalls and logging. Loader-wait time is CPU wait and may overlap
    GPU execution; it is explicitly not a claim of GPU idle percentage.
    """
    model.train(training)
    previous_mode = torch.is_grad_enabled()
    torch.set_grad_enabled(training)
    began = time.perf_counter()
    batches = CudaBatches(loader)
    startup_seconds = time.perf_counter() - began
    if reporter:
        reporter.update(stage="warmup / attention compilation" if warmup else "measuring", done=0, total=warmup+steps)
    events = []
    finite = torch.ones((), device="cuda", dtype=torch.bool)
    pending_losses = []  # Keep tiny device scalars until the next metrics upload.
    sums = torch.zeros(18, device="cuda", dtype=torch.float64)
    counts = torch.zeros(18, device="cuda", dtype=torch.int64)
    mae_sums = torch.zeros_like(sums)
    squared_sums = torch.zeros_like(sums)
    samples = measured = consumed = 0
    exhausted = False
    begin_measure = None
    torch.cuda.reset_peak_memory_stats()

    def publish_metrics():
        """Flush ten-step loss observations together with cumulative sufficient-statistic metrics."""
        prefix = "train/" if training else "validation/"
        for observed_step, observed_loss in pending_losses:
            reporter.log({prefix + "loss_sample": float(observed_loss)}, observed_step if training else log_base)
        pending_losses.clear()
        elapsed = max(time.perf_counter() - (begin_measure or began), 1e-9)
        values = {
            prefix + "loss": float((sums / counts.clamp_min(1))[counts > 0].mean()),
            prefix + "samples_per_second": samples / elapsed,
            prefix + "samples": samples,
        }
        # Accumulate on device each step; materialize full metrics only at the requested cadence.
        from .shards.phase1_contract import CHANNELS
        for index, name in enumerate(CHANNELS):
            if counts[index] > 0:
                values[prefix + name + "/loss"] = float(sums[index] / counts[index])
                values[prefix + name + "/mae"] = float(mae_sums[index] / counts[index])
                values[prefix + name + "/rmse"] = float((squared_sums[index] / counts[index]).sqrt())
        if optimizer:
            values["learning_rate"] = optimizer.param_groups[0]["lr"]
        reporter.log(values, log_base + consumed if training else log_base)

    try:
        for step in range(warmup + steps):
            if stop_path and stop_path.exists():
                break
            if step == warmup:
                torch.cuda.synchronize()
                begin_measure = time.perf_counter()
                wait_start = batches.wait_seconds
                if reporter:
                    reporter.update(stage="measuring training" if training else "measuring validation")
            try:
                batch = batches.pop()
            except StopIteration:
                exhausted = True
                break
            start, end = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
            start.record()
            if before_step is not None:
                before_step(consumed)  # Set the learning rate before this optimizer update.
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=precision == "bf16"):
                _, prediction = model(batch.visible, batch.valid, batch.feature_ids, batch.hidden_ids, batch.time, batch.time_valid)
                loss, batch_sums, batch_counts = reconstruction_loss(prediction, batch)
            if training:
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
            end.record()
            consumed += 1
            if on_step is not None:
                on_step(consumed)  # Caller checkpoints only at its configured cadence.
            if profiler is not None:
                profiler.step()  # Diagnostic runs only; never compare trace overhead as ordinary throughput.
            if step >= warmup:
                events.append((start, end))
                finite &= torch.isfinite(loss.detach())
                if (log_base + consumed) % loss_every == 0:
                    pending_losses.append((log_base + consumed, loss.detach()))
                sums += batch_sums
                counts += batch_counts
                decoded = prediction.detach().float()
                error = torch.where(batch.target_valid, decoded - batch.targets, 0)
                mae_sums.scatter_add_(0, batch.hidden_ids.flatten(), error.abs().sum(-1).double().flatten())
                squared_sums.scatter_add_(0, batch.hidden_ids.flatten(), error.square().sum(-1).double().flatten())
                measured += 1
                samples += len(batch.visible)
            # Enqueue the next input while current kernels execute, rather than synchronizing each step.
            if step + 1 < warmup + steps:
                batches.advance()
            if reporter and (step + 1) % loss_every == 0:
                torch.cuda.synchronize()
                if not torch.isfinite(loss):
                    raise FloatingPointError("Nonfinite loss during measured run.")
                elapsed = time.perf_counter() - (begin_measure or began)
                metrics = dict(loss=float(loss.detach()), samples_per_second=samples/max(elapsed, 1e-9),
                               peak_gib=torch.cuda.max_memory_allocated()/2**30,
                               loader_wait_pct=100*(batches.wait_seconds-(wait_start if begin_measure else 0))/max(elapsed, 1e-9))
                reporter.update(stage="training" if training else "validation", done=step+1,
                                total=warmup+steps, metrics=metrics,
                                learning_rate=optimizer.param_groups[0]["lr"] if optimizer else None)
            if reporter and (log_base + consumed if training else consumed) % metrics_every == 0:
                publish_metrics()
            if begin_measure and time.perf_counter()-begin_measure >= seconds_limit:
                break
        torch.cuda.synchronize()
        if not measured:
            raise ValueError("No measured batches completed after warmup.")
        elapsed = time.perf_counter() - begin_measure
        if not finite:
            raise FloatingPointError("Nonfinite loss in profiling window.")
        if reporter:
            publish_metrics()  # Include the final partial interval at an epoch/window boundary.
        milliseconds = np.array([start.elapsed_time(end) for start, end in events])
        valid = counts > 0
        per_feature = sums/counts.clamp_min(1)
        result = dict(samples=samples, measured_steps=measured, consumed_batches=consumed, exhausted=exhausted,
                      seconds=elapsed, startup_seconds=startup_seconds,
                      warmup_seconds=begin_measure-began-startup_seconds, samples_per_second=samples/elapsed,
                      compute_ms_mean=float(milliseconds.mean()), compute_ms_p95=float(np.quantile(milliseconds, .95)),
                      compute_duty_pct=min(100., float(milliseconds.sum()/10/elapsed)),
                      loader_wait_pct=100*(batches.wait_seconds-wait_start)/elapsed,
                      peak_gib=torch.cuda.max_memory_allocated()/2**30,
                      reserved_gib=torch.cuda.max_memory_reserved()/2**30,
                      loss=float(per_feature[valid].mean()), feature_loss=per_feature.cpu().tolist(),
                      feature_mae=(mae_sums/counts.clamp_min(1)).cpu().tolist(),
                      feature_rmse=(squared_sums/counts.clamp_min(1)).sqrt().cpu().tolist(),
                      target_counts=counts.cpu().tolist())
        for key in ("feature_loss", "feature_mae", "feature_rmse"):
            result[key] = [value if count else None for value, count in zip(result[key], result["target_counts"])]
        if reporter:
            with reporter.lock:
                readings = [reading for reading in reporter.gpu_samples if reading[0] >= begin_measure]
            result["gpu_util_mean"] = float(np.mean([r[1] for r in readings])) if readings else None
            result["power_w_mean"] = float(np.mean([r[3] for r in readings])) if readings else None
        return result
    finally:
        torch.set_grad_enabled(previous_mode)
