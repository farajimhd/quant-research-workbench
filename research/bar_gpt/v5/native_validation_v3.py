"""All-visible reconstruction MAE in original sample units; no training objective."""

import math
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import time
import torch

from .shards.contract import FRAMES
from .shards.phase1_contract import CHANNELS
from .price_relative_v3 import PriceRelative, CONTRACT as PRICE_RELATIVE_CONTRACT

PRICES = (0, 1, 2, 3, 6, 7, 8, 9, 12, 13, 14, 15)
ACTIVITY = (4, 5, 10, 11, 16, 17)
FRAME_NAMES = ("100ms", "1s", "10s", "1m", "1h", "daily")
CONTRACT = dict(
    version="all-visible-local-reference-reconstruction-mae-v3",
    inputs="all18 market columns; unmasked full validation",
    targets="same raw input window; original source-valid observations only",
    price_inverse="full-window close reference * exp(prediction/100)",
    activity_inverse="expm1(prediction + ln(1+full-window feature median))",
    reference_timing="computed from all source-valid raw observations BEFORE masking",
    price_units="USD per share on the input sample's split-adjusted basis",
    volume_size_units="original stored source quantity; no invented lot conversion",
    count_units="observed events per bar",
    negative_predictions="retained without clipping",
    overflow="MAE unavailable for affected head/frame; explicit count",
    purpose="reference-conditioned reconstruction, not absolute-scale recovery or forecasting",
    backend="bounded asynchronous CPU Float64 reduction; one GPU forward",
    price_relative=PRICE_RELATIVE_CONTRACT,
)


def inverse(prediction, reference, stats):
    if prediction.ndim != 3 or prediction.shape[1:] != (18, 64):
        raise ValueError("Expected [B,18,64] predictions")
    if reference.shape != (len(prediction), 1, 1) or stats.shape != (
        len(prediction),
        6,
    ):
        raise ValueError("V3 inverse needs per-window price and quantity references")
    result = prediction.to(dtype=torch.float64, copy=True)
    result[:, PRICES] = reference * torch.exp(result[:, PRICES] / 100)
    result[:, ACTIVITY] = torch.expm1(
        result[:, ACTIVITY] + torch.log1p(stats[:, :, None])
    )
    return result


class NativeMAE:
    """Exact observation-weighted reductions by timeframe/head; no per-step sync."""

    def __init__(self, device):
        self.frames = torch.tensor(FRAMES, device=device)
        self.sums = torch.zeros((len(FRAMES), 18), device=device, dtype=torch.float64)
        self.counts = torch.zeros_like(self.sums, dtype=torch.int64)
        self.overflows = torch.zeros_like(self.counts)
        self.price_relative = PriceRelative()

    def add(self, prediction, batch):
        decoded = inverse(prediction, batch.native_reference, batch.native_stats)
        errors = decoded.sub_(batch.native_targets).abs_()
        finite = torch.isfinite(errors)
        mask = batch.native_valid
        frame = torch.searchsorted(self.frames, batch.native_frames)
        self.price_relative.add(errors, batch.native_targets, mask, frame)
        keys = frame[:, None] * 18 + torch.arange(18, device=decoded.device)
        errors.masked_fill_(~(mask & finite), 0)
        self.sums.flatten().scatter_add_(0, keys.flatten(), errors.sum(-1).flatten())
        self.counts.flatten().scatter_add_(0, keys.flatten(), mask.sum(-1).flatten())
        self.overflows.flatten().scatter_add_(
            0, keys.flatten(), (mask & ~finite).sum(-1).flatten()
        )

    def metrics(self, prefix="validation_full_raw_mae"):
        sums, counts, bad = (
            x.cpu().tolist() for x in (self.sums, self.counts, self.overflows)
        )
        result = {}
        for frame, s, n, b in zip(FRAME_NAMES, sums, counts, bad):
            for channel, total, count, overflow in zip(CHANNELS, s, n, b):
                key = f"{prefix}/{frame}/{channel}"
                result[key] = (
                    total / count
                    if count and not overflow and math.isfinite(total)
                    else None
                )
                result[f"{prefix}_counts/{frame}/{channel}"] = count
                result[f"{prefix}_overflows/{frame}/{channel}"] = overflow
                if not math.isfinite(total):
                    result[f"{prefix}_sum_overflow/{frame}/{channel}"] = 1
        result.update(self.price_relative.metrics(prefix, FRAME_NAMES, CHANNELS))
        return result


class AsyncNativeMAE:
    """Overlap pinned prediction readback/CPU reduction with the next inference.

    Raw truth/reference/units remain in their existing pinned CPU batch. Only
    decoder outputs are copied back; no second source read or model forward.
    FIFO merges and bounded backpressure retain every observation and exact
    per-publication counts. Worker exceptions propagate instead of losing data.
    """

    def __init__(self, device="cuda", workers=4, max_pending=4):
        self.stream = torch.cuda.Stream(device=device)
        self.pool = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="native-mae"
        )
        self.pending = deque()
        self.total = NativeMAE("cpu")
        self.max_pending = max_pending
        self.backpressure_seconds = 0.0

    def copy_prediction(self, prediction):
        ready = torch.cuda.Event()
        ready.record(torch.cuda.current_stream())
        output = torch.empty_like(prediction, device="cpu", pin_memory=True)
        with torch.cuda.stream(self.stream):
            self.stream.wait_event(ready)
            output.copy_(prediction, non_blocking=True)
            done = torch.cuda.Event()
            done.record(self.stream)
            prediction.record_stream(self.stream)
        return output, done

    @staticmethod
    def reduce(prediction, done, metadata):
        done.synchronize()  # Only this CPU worker waits for its own D2H copy.
        with torch.inference_mode():
            partial = NativeMAE("cpu")
            partial.add(prediction, metadata)
        return partial.sums, partial.counts, partial.overflows, partial.price_relative

    def merge_one(self):
        values = self.pending.popleft().result()
        for target, value in zip(
            (self.total.sums, self.total.counts, self.total.overflows), values
        ):
            target.add_(value)
        self.total.price_relative.merge(values[3])

    def add(self, prediction, batch):
        if len(self.pending) >= self.max_pending:
            began = time.perf_counter()
            self.merge_one()
            self.backpressure_seconds += time.perf_counter() - began
        names = (
            "native_targets",
            "native_valid",
            "native_reference",
            "native_stats",
            "native_frames",
        )
        metadata = SimpleNamespace(**{name: getattr(batch, name) for name in names})
        if any(getattr(metadata, name).device.type != "cpu" for name in names):
            raise ValueError("Async MAE metadata must remain on CPU")
        output, done = self.copy_prediction(prediction)
        self.pending.append(self.pool.submit(self.reduce, output, done, metadata))

    def metrics(self, prefix="validation_full_raw_mae"):
        self.flush()
        result = self.total.metrics(prefix)
        result[prefix + "_backend/backpressure_seconds"] = self.backpressure_seconds
        return result

    def flush(self):
        while self.pending:
            self.merge_one()

    def close(self):
        try:
            while self.pending:
                self.merge_one()
        finally:
            self.pool.shutdown(wait=True)


def evaluate_full(model, batch, accumulator, precision="bf16"):
    """Standalone full-input helper; the campaign reuses its single forward."""
    if model.training or torch.is_grad_enabled():
        raise ValueError("Native reconstruction requires evaluation/inference mode")
    ids = torch.arange(18, device=batch.native_values.device).expand(
        len(batch.visible), -1
    )
    with torch.autocast("cuda", dtype=torch.bfloat16, enabled=precision == "bf16"):
        _, prediction = model(
            batch.native_values,
            batch.native_valid,
            ids,
            ids,
            batch.time,
            batch.time_valid,
        )
    accumulator.add(prediction, batch)
