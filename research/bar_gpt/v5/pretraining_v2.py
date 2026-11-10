"""Encoder v2: frozen training-population z scores for every market feature."""

from dataclasses import dataclass
import torch

from .pretraining_v1 import PreparedBatch, validate_market, CLOSES
from .transforms_v1 import BarInputTransformV1
from .shards.contract import FRAMES

TRANSFORM = "all18-per-timeframe-training-population-zscore-v2"


@dataclass(frozen=True)
class ZScoreStats:
    mean: torch.Tensor
    std: torch.Tensor

    def __post_init__(self):
        for x in (self.mean, self.std):
            if (
                x.shape != (6, 18)
                or x.device.type != "cpu"
                or not torch.isfinite(x).all()
            ):
                raise ValueError("Z-score statistics require finite CPU [6,18] tensors")
        if not (self.std > 0).all():
            raise ValueError("Z-score standard deviations must be positive")

    # Serialized by the duplicated campaign using its existing strict fields.
    @property
    def median(self):
        return self.mean

    @property
    def scale(self):
        return self.std


def frame_indices(timeframe_us, size):
    frames = torch.as_tensor(timeframe_us, dtype=torch.int64).expand(size).clone()
    allowed = torch.tensor(FRAMES, dtype=torch.int64)
    if not torch.isin(frames, allowed).all():
        raise ValueError("Unknown z-score timeframe")
    return frames, torch.searchsorted(allowed, frames)


def prepare_batch(
    values,
    valid,
    end_us,
    timeframe_us,
    stats,
    generator,
    *,
    mask_rate=0.6,
    native_validation=False
):
    """Preserve v1 whole-column masking/order and nine UTC inputs exactly."""
    validate_market(values, valid)
    if not 0 <= mask_rate < 1:
        raise ValueError("Invalid mask rate")
    if end_us.device.type != "cpu":
        raise ValueError("Raw clocks must remain CPU")
    batch = len(values)
    frames, index = frame_indices(timeframe_us, batch)
    mean, std = stats.mean[index].double(), stats.std[index].double()
    scaled = torch.where(
        valid, (values.double() - mean[:, None]) / std[:, None], 0
    ).float()
    transformed = BarInputTransformV1()(scaled, valid, end_us, timeframe_us)
    columns, evidence = (
        scaled.transpose(1, 2).contiguous(),
        valid.transpose(1, 2).contiguous(),
    )
    if native_validation:
        ids = torch.arange(18).expand(batch, -1).clone()
        return PreparedBatch(
            columns,
            evidence,
            ids,
            ids,
            columns,
            evidence,
            transformed.values[..., 18:].contiguous(),
            transformed.valid[..., 18:].contiguous(),
            native_values=columns,
            native_valid=evidence,
            native_targets=values.transpose(1, 2).contiguous(),
            native_reference=torch.empty(0),
            native_frames=frames,
            native_stats=torch.stack((mean, std)),
        )
    expected = 18 * mask_rate
    masked = int(expected) + int(torch.rand((), generator=generator) < expected % 1)
    kept = 18 - masked
    close_available = valid[..., CLOSES].any(dim=1)
    if not close_available.any(dim=1).all():
        raise ValueError("Each sample needs an observed closing price")
    eligible = torch.zeros((batch, 18), dtype=torch.bool)
    eligible[:, CLOSES] = close_available
    order = torch.rand((batch, 18), generator=generator).argsort(dim=1)
    for _ in range(256):
        rejected = ~eligible.gather(1, order[:, :kept]).any(dim=1)
        if not rejected.any():
            break
        order[rejected] = torch.rand(
            (int(rejected.sum()), 18), generator=generator
        ).argsort(dim=1)
    else:
        raise RuntimeError("No valid visible-price mask within bounded retries")
    feature_ids, hidden_ids = order[:, :kept], order[:, kept:]
    a, b = feature_ids[..., None].expand(-1, -1, 64), hidden_ids[..., None].expand(
        -1, -1, 64
    )
    return PreparedBatch(
        columns.gather(1, a),
        evidence.gather(1, a),
        feature_ids,
        hidden_ids,
        columns.gather(1, b),
        evidence.gather(1, b),
        transformed.values[..., 18:].contiguous(),
        transformed.valid[..., 18:].contiguous(),
    )
