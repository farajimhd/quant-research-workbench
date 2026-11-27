"""Masked Phase 2 forecast losses with optional causal candle supervision."""
from dataclasses import dataclass
import math

import torch
from torch.nn import functional as F

from ..phase0_v7.transforms import coordinates
from ..phase1_v29.objectives import masked_mean
from .model import HORIZONS, objective


@dataclass(frozen=True)
class LossWeights:
    price: float = .5
    activity: float = .5
    coordinate: float = .1
    direction: float = 0.
    range: float = 0.
    ohlc: float = 0.
    from_last: float = 0.
    anchored_direction: bool = False

    def __post_init__(self):
        if (any(not math.isfinite(v) or v < 0 for v in vars(self).values())
                or not math.isclose(self.price + self.activity, 1.)):
            raise ValueError("Loss weights must be finite and nonnegative, with price + activity = 1")
        if self.anchored_direction and (not self.direction or self.from_last):
            raise ValueError("Anchored direction requires one nonzero combined direction weight")

    @property
    def uses_anchor(self):
        return bool(self.from_last or self.anchored_direction)

    @classmethod
    def parse(cls, value):
        parts = value.split(",")
        if len(parts) != 7:
            raise ValueError("Expected price,activity,coordinate,direction,range,ohlc,from_last")
        try:
            return cls(*(float(part) for part in parts))
        except (TypeError, ValueError) as error:
            raise ValueError("Expected price,activity,coordinate,direction,range,ohlc,from_last") from error


def candle_fields(logs, code, valid, code_valid):
    """Build close, activity, and range values and their source-valid masks."""
    structured = logs.unflatten(-1, (3, 6))
    observed = valid.unflatten(-1, (3, 6))
    opened, high, low, close = structured[..., :4].unbind(-1)
    open_ok, high_ok, low_ok, close_ok = observed[..., :4].unbind(-1)
    activity = torch.stack((logs[..., 4], code[..., 5], logs[..., 10],
                            logs[..., 11], logs[..., 16], logs[..., 17]), -1)
    activity_ok = torch.stack((valid[..., 4], code_valid[..., 5], valid[..., 10],
                               valid[..., 11], valid[..., 16], valid[..., 17]), -1)
    return (opened, high, low, close, activity), (open_ok, high_ok, low_ok, close_ok, activity_ok)


def relative_direction(pred, target, base, valid, scale):
    """Anchor both moves to the same known value and mask missing fields first."""
    predicted = torch.where(valid, pred-base, 0)
    actual = torch.where(valid, target-base, 0)
    sign = actual.sign()
    margin = actual.abs().clamp_max(scale)
    residual = torch.where(sign == 0, predicted, (margin-sign*predicted).relu()) / scale
    error = F.smooth_l1_loss(residual, torch.zeros_like(residual), reduction="none")
    means, counts = [], []
    for selected in (valid & (sign < 0), valid & (sign == 0), valid & (sign > 0)):
        count = selected.sum()
        counts.append(count > 0)
        means.append(torch.where(selected, error, 0).sum() / count.clamp_min(1))
    return sum(means) / torch.stack(counts).sum().clamp_min(1)


def structural_terms(out, logs, valid, anchors, weights):
    safe = torch.where(valid, logs, 0)
    target_code, code_valid = coordinates(safe, valid)
    (o, h, l, c, a), (ov, hv, lv, cv, av) = candle_fields(safe, target_code, valid, code_valid)
    (po, ph, pl, pc, pa), _ = candle_fields(out["logs"], out["code"], valid, code_valid)
    zero = out["logs"].new_zeros(())
    direction = range_loss = ohlc = from_last = zero
    if weights.direction:
        # Roll only within a timeframe. Consecutive forecasts are both model
        # outputs; the previous realized future bar is used only in the label.
        close_pairs, activity_pairs = [], []
        for close_valid, activity_valid in zip(cv.split(HORIZONS, 1), av.split(HORIZONS, 1)):
            close_pairs.append(torch.cat((torch.zeros_like(close_valid[:, :1]),
                                          close_valid[:, 1:] & close_valid[:, :-1]), 1))
            activity_pairs.append(torch.cat((torch.zeros_like(activity_valid[:, :1]),
                                             activity_valid[:, 1:] & activity_valid[:, :-1]), 1))
        body = relative_direction(pc-po, c-o, 0, cv & ov, .001)
        path = relative_direction(pc-pc.roll(1, 1), c-c.roll(1, 1), 0, torch.cat(close_pairs, 1), .001)
        activity = relative_direction(pa-pa.roll(1, 1), a-a.roll(1, 1), 0, torch.cat(activity_pairs, 1), .1)
        direction = (body + path + activity) / 3
    if weights.range:
        error = ((ph - pl) - (h - l)) / .001
        range_loss = masked_mean(F.smooth_l1_loss(error, torch.zeros_like(error), reduction="none"), hv & lv)
    if weights.ohlc:
        violations = torch.stack((po-ph, pc-ph, pl-po, pl-pc, pl-ph), -1).relu() / .001
        structural = F.smooth_l1_loss(violations, torch.zeros_like(violations), reduction="none").mean(-1)
        consistent = (h >= torch.maximum(o, c)) & (l <= torch.minimum(o, c))
        ohlc = masked_mean(structural, ov & hv & lv & cv & consistent)
    if weights.uses_anchor:
        anchor_logs, anchor_valid = anchors
        anchor_logs = torch.where(anchor_valid, anchor_logs, 0)
        anchor_code, anchor_code_valid = coordinates(anchor_logs, anchor_valid)
        (_, ah, al, ac, aa), (_, ahv, alv, acv, aav) = candle_fields(
            anchor_logs, anchor_code, anchor_valid, anchor_code_valid)
        close_move = relative_direction(pc, c, ac, cv & acv, .001)
        activity_move = relative_direction(pa, a, aa, av & aav, .1)
        range_move = relative_direction(ph-pl, h-l, ah-al, hv & lv & ahv & alv, .001)
        from_last = (close_move + activity_move + range_move) / 3
    return direction, range_loss, ohlc, from_last


def forecast_objective(decoder, out, logs, valid, weights, anchors=None):
    base = objective(decoder, out, logs, valid, price=weights.price, activity=weights.activity,
                     coordinate=weights.coordinate)
    if not (weights.direction or weights.range or weights.ohlc or weights.from_last):
        return base
    direction, candle_range, ohlc, from_last = structural_terms(out, logs, valid, anchors, weights)
    if weights.anchored_direction:
        direction = .5 * (direction + from_last)
    return base + weights.direction * direction + weights.range * candle_range + weights.ohlc * ohlc + weights.from_last * from_last
