"""CPU masking and agreed market scaling, separate from the neural architecture.

Raw batches come from the fixed-array shard contract, never from a DataFrame.
Only compact visible inputs, time context, and masked loss targets move to GPU.
No hidden-input placeholders are sent to the encoder.
"""

from dataclasses import dataclass, fields

import torch
from torch import Tensor

from research.bar_gpt.v5.transforms_v1 import BarInputTransformV1


PRICES = (0, 1, 2, 3, 6, 7, 8, 9, 12, 13, 14, 15)
CLOSES = (3, 9, 15)
ACTIVITY = (4, 5, 10, 11, 16, 17)
ACTIVITY_GROUPS = ((4,), (5,), (10, 16), (11, 17))


@dataclass(frozen=True)
class ActivityStats:
    """Frozen CPU Float32 vectors [6], in ACTIVITY order; fit on training data only.

    median and scale describe log1p observations. Bid/ask sizes share statistics;
    bid/ask counts share statistics. No per-window or per-timeframe standardization.
    """

    median: Tensor
    scale: Tensor

    def __post_init__(self):
        for item in (self.median, self.scale):
            if item.shape != (6,) or item.device.type != "cpu" or not torch.isfinite(item).all():
                raise ValueError("Activity statistics must be finite CPU vectors [6].")
        if not (self.scale > 0).all():
            raise ValueError("Activity scales must be strictly positive.")


def validate_market(values: Tensor, valid: Tensor) -> None:
    """Reject invalid observed values in raw CPU [B,64,21] batches before logarithms."""
    if values.device.type != "cpu" or valid.device.type != "cpu":
        raise ValueError("Masking/scaling requires CPU batches before GPU transfer.")
    if values.ndim != 3 or values.shape[1:] != (64, 21) or len(values) == 0:
        raise ValueError("Expected a nonempty raw [B,64,21] market batch.")
    if not values.is_floating_point() or valid.shape != values.shape or valid.dtype != torch.bool:
        raise ValueError("Expected floating values and matching bool evidence masks.")
    if not (torch.isfinite(values) | ~valid).all():
        raise ValueError("Observed market values must be finite.")
    if ((values[..., PRICES] <= 0) & valid[..., PRICES]).any():
        raise ValueError("Observed prices must be positive.")
    if ((values[..., ACTIVITY + (20,)] < 0) & valid[..., ACTIVITY + (20,)]).any():
        raise ValueError("Observed activity and halt counts must be nonnegative.")
    flags = values[..., 18:20]
    if (~((flags == 0) | (flags == 1)) & valid[..., 18:20]).any():
        raise ValueError("Halt state observations must be binary.")


def fit_activity_stats(training_values: Tensor, training_valid: Tensor) -> ActivityStats:
    """Fit exact log-space medians/IQRs on caller-selected TRAINING observations.

    Inputs: CPU floats and bools [N,64,21], e.g. a declared bounded training-only
    calibration sample. No sampling or validation-data selection occurs here.
    This function does not load the entire 1 TB corpus; the caller owns sample size
    and provenance. A missing activity family fails explicitly; zero IQR uses 1.
    """
    validate_market(training_values, training_valid)
    median, scale = torch.empty(6), torch.empty(6)
    for group in ACTIVITY_GROUPS:  # Four semantic groups, not a loop over observations.
        raw = training_values[..., group]
        observed = raw[training_valid[..., group]].double()
        if observed.numel() == 0:
            raise ValueError(f"No training observations for activity group {group}.")
        quantiles = torch.quantile(torch.log1p(observed), torch.tensor([0.25, 0.5, 0.75], dtype=torch.float64))
        spread = quantiles[2] - quantiles[0]
        for column in group:
            index = ACTIVITY.index(column)
            median[index] = quantiles[1]
            scale[index] = spread if spread > 0 else 1
    return ActivityStats(median, scale)


@dataclass(frozen=True)
class PreparedBatch:
    """Packed tensors; K visible columns, M hidden columns, K+M=21.

    visible/valid [B,K,64]; feature_ids [B,K]; hidden_ids [B,M]; targets and
    target_valid [B,M,64]; time/time_valid [B,64,9]. Values are float32, masks bool,
    IDs int64. Targets are never passed into the encoder or decoder.
    """

    visible: Tensor
    valid: Tensor
    feature_ids: Tensor
    hidden_ids: Tensor
    targets: Tensor
    target_valid: Tensor
    time: Tensor
    time_valid: Tensor

    def pin_memory(self) -> "PreparedBatch":
        """Let DataLoader's pinning thread prepare asynchronous GPU transfers."""
        return PreparedBatch(**{field.name: getattr(self, field.name).pin_memory() for field in fields(self)})

    def to(self, device: torch.device | str, *, pin_memory: bool = False) -> "PreparedBatch":
        """Transfer only packed tensors; optionally pin CPU buffers for CUDA transfer."""
        moved = {}
        for field in fields(self):  # Eight tensors per batch; no per-sample transfer loop.
            item = getattr(self, field.name)
            if pin_memory and item.device.type == "cpu" and torch.device(device).type == "cuda":
                item = item.pin_memory()
            moved[field.name] = item.to(device, non_blocking=pin_memory)
        return PreparedBatch(**moved)


def prepare_batch(values: Tensor, valid: Tensor, end_us: Tensor, timeframe_us: Tensor | int,
                  stats: ActivityStats, generator: torch.Generator, *, mask_rate: float = 0.6) -> PreparedBatch:
    """Scale and pack raw CPU shards into visible feature tokens and hidden targets.

    Raw shapes: values/valid [B,64,21], end_us [B,64], timeframe_us scalar or [B].
    Masks are whole columns, independently randomized per sample. A shared batch
    count avoids padding: stochastic rounding gives the requested rate in expectation
    (at .6: hide 12 with probability .4, 13 with probability .6). Sampling is uniform
    conditional on retaining at least one observed closing-price column. This explicit
    constraint prevents a price reference from depending on hidden reconstruction targets.
    mask_rate=0 prepares all 21 columns for downstream embedding extraction.
    """
    validate_market(values, valid)
    if not 0 <= mask_rate < 1:
        raise ValueError("mask_rate must be in [0,1).")
    if end_us.device.type != "cpu" or (isinstance(timeframe_us, Tensor) and timeframe_us.device.type != "cpu"):
        raise ValueError("Raw clocks and timeframe metadata must be on CPU.")
    batch = len(values)
    expected = 21 * mask_rate
    masked = int(expected) + int(torch.rand((), generator=generator) < expected % 1)
    if masked == 21:
        raise ValueError("Sampled mask hides all columns; a visible price reference is required.")
    kept = 21 - masked
    close_available = valid[..., CLOSES].any(dim=1)  # [B,3], evidence independent of market values.
    if not close_available.any(dim=1).all():
        raise ValueError("Each sample needs at least one valid closing price; no fallback is invented.")
    eligible = torch.zeros((batch, 21), dtype=torch.bool)
    eligible[:, CLOSES] = close_available
    order = torch.rand((batch, 21), generator=generator).argsort(dim=1)
    # Rejection sampling preserves conditional uniformity; only failed rows are redrawn.
    for _ in range(256):
        rejected = ~eligible.gather(1, order[:, :kept]).any(dim=1)
        if not rejected.any():
            break
        order[rejected] = torch.rand((int(rejected.sum()), 21), generator=generator).argsort(dim=1)
    else:
        raise RuntimeError("Could not sample a valid visible-price mask within the bounded retries.")
    feature_ids, hidden_ids = order[:, :kept], order[:, kept:]
    visible_columns = torch.zeros((batch, 21), dtype=torch.bool).scatter_(1, feature_ids, True)
    reference_mask = valid[..., CLOSES] & visible_columns[:, None, CLOSES]
    closes = torch.where(reference_mask, values[..., CLOSES], torch.nan).flatten(1).double()
    reference = torch.nanquantile(closes, 0.5, dim=1)[:, None, None]  # [B,1,1], visible median only.

    scaled = torch.where(valid, values, 0).double()  # Full CPU targets; never sent as encoder input.
    prices = torch.where(valid[..., PRICES], scaled[..., PRICES], reference)
    scaled[..., PRICES] = 100 * torch.log(prices / reference)  # Preserve common bid/ask geometry.
    scaled[..., ACTIVITY] = (torch.log1p(scaled[..., ACTIVITY]) - stats.median) / stats.scale
    scaled[..., 20] = torch.log1p(scaled[..., 20])  # Halt states 18/19 remain binary.
    scaled = torch.where(valid, scaled, 0).float()  # Invalid source fields remain masked zeros.
    # Reuse the UTC contract for validation/time features; this preparation runs on CPU.
    transformed = BarInputTransformV1()(scaled, valid, end_us, timeframe_us)
    columns, evidence = scaled.transpose(1, 2), valid.transpose(1, 2)  # [B,21,64].
    visible_index = feature_ids[..., None].expand(-1, -1, 64)
    hidden_index = hidden_ids[..., None].expand(-1, -1, 64)
    if masked and not evidence.gather(1, hidden_index).any():
        raise ValueError("Batch has no observed hidden reconstruction targets.")
    return PreparedBatch(
        columns.gather(1, visible_index), evidence.gather(1, visible_index), feature_ids, hidden_ids,
        columns.gather(1, hidden_index), evidence.gather(1, hidden_index),
        transformed.values[..., 21:].contiguous(), transformed.valid[..., 21:].contiguous(),
    )
