"""All-visible reconstruction MAE in original sample units; no training objective."""

import math
import torch

from .shards.contract import FRAMES
from .shards.phase1_contract import CHANNELS

PRICES = (0, 1, 2, 3, 6, 7, 8, 9, 12, 13, 14, 15)
ACTIVITY = (4, 5, 10, 11, 16, 17)
FRAME_NAMES = ("100ms", "1s", "10s", "1m", "1h", "daily")
CONTRACT = dict(
    version="all-visible-native-reconstruction-mae-v1",
    inputs="all18 market columns, canonical order; no random feature masking",
    targets="same raw input window; original source-valid observations only",
    price_inverse="visible-all close median * exp(prediction / 100)",
    activity_inverse="expm1(prediction * frozen scale + frozen median)",
    price_units="USD per share on the input sample's split-adjusted basis",
    volume_size_units="original stored source quantity; no invented lot conversion",
    count_units="observed events per bar",
    negative_predictions="retained without clipping",
    overflow="MAE unavailable for affected head/frame, overflow count reported",
    purpose="unmasked reconstruction diagnostic, not forecasting or masked-validation score",
)


def prepare_full(values, valid, timeframe_us, stats):
    """Retain raw truth and scale all visible columns without consuming any RNG."""
    closes = torch.where(valid[..., (3, 9, 15)], values[..., (3, 9, 15)], torch.nan)
    reference = torch.nanquantile(closes.flatten(1).double(), 0.5, dim=1)[:, None, None]
    scaled = torch.where(valid, values, 0).double()
    prices = torch.where(valid[..., PRICES], scaled[..., PRICES], reference)
    scaled[..., PRICES] = 100 * torch.log(prices / reference)
    scaled[..., ACTIVITY] = (
        torch.log1p(scaled[..., ACTIVITY]) - stats.median
    ) / stats.scale
    scaled = torch.where(valid, scaled, 0).float()
    frames = (
        torch.as_tensor(timeframe_us, dtype=torch.int64).expand(len(values)).clone()
    )
    if not torch.isin(frames, torch.tensor(FRAMES)).all():
        raise ValueError("Unknown native-validation timeframe")
    return dict(
        native_values=scaled.transpose(1, 2).contiguous(),
        native_valid=valid.transpose(1, 2).contiguous(),
        native_targets=values.transpose(1, 2).contiguous(),
        native_reference=reference,
        native_frames=frames,
        native_stats=torch.stack((stats.median, stats.scale)).double(),
    )


def inverse(prediction, reference, stats):
    """Invert all18 decoder channels [B,18,64], without rounding or clipping."""
    if prediction.ndim != 3 or prediction.shape[1:] != (18, 64):
        raise ValueError("Expected predictions [B,18,64] in canonical column order")
    result = prediction.to(dtype=torch.float64, copy=True)
    result[:, PRICES] = reference * torch.exp(result[:, PRICES] / 100)
    result[:, ACTIVITY] = torch.expm1(
        result[:, ACTIVITY] * stats[1, :, None] + stats[0, :, None]
    )
    return result


class NativeMAE:
    """Exact observation-weighted reductions by timeframe/head; no per-step sync."""

    def __init__(self, device):
        self.frames = torch.tensor(FRAMES, device=device)
        self.sums = torch.zeros((len(FRAMES), 18), device=device, dtype=torch.float64)
        self.counts = torch.zeros_like(self.sums, dtype=torch.int64)
        self.overflows = torch.zeros_like(self.counts)

    def add(self, prediction, batch):
        decoded = inverse(prediction, batch.native_reference, batch.native_stats)
        errors = decoded.sub_(batch.native_targets).abs_()
        finite = torch.isfinite(errors)
        mask = batch.native_valid
        frame = torch.searchsorted(self.frames, batch.native_frames)
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
        return result


def evaluate_full(model, batch, accumulator, precision="bf16"):
    """One extra inference-only forward over all columns, never a random mask."""
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
