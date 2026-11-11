"""V3: full-window references before unrestricted whole-column masking.

The normalizer intentionally sees all source-valid values before augmentation.
Reference-conditioned targets are not absolute-scale prediction targets. Reference
arrays are evaluation metadata, never additional encoder or decoder tokens.
"""

from dataclasses import dataclass, field
import torch

from .pretraining_v1 import PreparedBatch, validate_market, PRICES, CLOSES, ACTIVITY
from .transforms_v1 import BarInputTransformV1
from .shards.contract import FRAMES

TRANSFORM = "premask-local-reference-column-mae-v3"
REFERENCE_POLICY = dict(
    version=TRANSFORM,
    prices="100*ln(price/shared median of all source-valid trade/bid/ask closes)",
    quantities="ln(1+quantity)-ln(1+per-feature source-valid 64-bar median)",
    timing="all references computed before random column masking",
    masking="uniform feature permutations; no protected feature or visible-close condition",
    empty_quantity="reference=0 placeholder only when no source-valid quantity exists",
    median="Float64 50th percentile with linear interpolation",
    raw_metrics="reference-conditioned reconstruction; not independent absolute-scale recovery",
)


@dataclass(frozen=True)
class LocalReferencePolicy:
    """No fitted global activity statistics; descriptors retain runner compatibility."""

    median: torch.Tensor = field(default_factory=lambda: torch.zeros(6))
    scale: torch.Tensor = field(default_factory=lambda: torch.ones(6))

    def __post_init__(self):
        if not torch.equal(self.median, torch.zeros(6)) or not torch.equal(
            self.scale, torch.ones(6)
        ):
            raise ValueError(
                "V3 uses local references, not fitted global activity statistics"
            )


def local_references(values, valid):
    """Vectorized source-valid medians, independent of augmentation RNG/masks."""
    closes = torch.where(valid[..., CLOSES], values[..., CLOSES], torch.nan)
    price = torch.nanquantile(closes.flatten(1).double(), 0.5, dim=1)[:, None, None]
    if not torch.isfinite(price).all() or not (price > 0).all():
        raise ValueError("Each raw window requires an observed positive closing price")
    activity = torch.where(valid[..., ACTIVITY], values[..., ACTIVITY], torch.nan)
    quantity = torch.nanquantile(activity.double(), 0.5, dim=1)
    quantity = torch.where(valid[..., ACTIVITY].any(dim=1), quantity, 0)
    if not torch.isfinite(quantity).all():
        raise ValueError("Invalid local quantity reference")
    return price, quantity


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
    validate_market(values, valid)
    if not isinstance(stats, LocalReferencePolicy):
        raise ValueError("V3 requires the local reference policy")
    if not 0 <= mask_rate < 1:
        raise ValueError("Mask rate must be in [0,1)")
    if end_us.device.type != "cpu" or (
        isinstance(timeframe_us, torch.Tensor) and timeframe_us.device.type != "cpu"
    ):
        raise ValueError("Raw clocks and timeframe metadata must remain CPU")
    frames = (
        torch.as_tensor(timeframe_us, dtype=torch.int64).expand(len(values)).clone()
    )
    if not torch.isin(frames, torch.tensor(FRAMES)).all():
        raise ValueError("Unknown timeframe")
    # This order is the v3 contract: references FIRST, augmentation SECOND.
    price_reference, quantity_reference = local_references(values, valid)
    scaled = torch.where(valid, values, 0).double()
    prices = torch.where(valid[..., PRICES], scaled[..., PRICES], price_reference)
    scaled[..., PRICES] = 100 * torch.log(prices / price_reference)
    scaled[..., ACTIVITY] = torch.log1p(scaled[..., ACTIVITY]) - torch.log1p(
        quantity_reference[:, None]
    )
    scaled = torch.where(valid, scaled, 0).float()
    transformed = BarInputTransformV1()(scaled, valid, end_us, timeframe_us)
    columns, evidence = (
        scaled.transpose(1, 2).contiguous(),
        valid.transpose(1, 2).contiguous(),
    )
    if native_validation:
        ids = torch.arange(18).expand(len(values), -1).clone()
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
            native_reference=price_reference,
            native_frames=frames,
            native_stats=quantity_reference,
        )
    expected = 18 * mask_rate
    masked = int(expected) + int(torch.rand((), generator=generator) < expected % 1)
    if masked == 18:
        raise ValueError(
            "Masking all columns simultaneously leaves no encoder market input"
        )
    order = torch.rand((len(values), 18), generator=generator).argsort(dim=1)
    feature_ids, hidden_ids = order[:, : 18 - masked], order[:, 18 - masked :]
    a, b = feature_ids[..., None].expand(-1, -1, 64), hidden_ids[..., None].expand(
        -1, -1, 64
    )
    if masked and not evidence.gather(1, b).any():
        raise ValueError("Batch has no observed hidden reconstruction targets")
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
