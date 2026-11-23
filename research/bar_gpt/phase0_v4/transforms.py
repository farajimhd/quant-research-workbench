"""Invertible single-bar coordinates, without decoder-side target references.

Input order is trade OHLC/volume/count, bid OHLC/size/count, ask OHLC/size/count.
Prices arrive as log(price); activities arrive as log1p(activity). Source-valid
values must already satisfy the canonical contract. We never clip source values.
"""
import torch
from ..phase1_v11.transforms import coordinates as price_coordinates

# Each triple is (bid, ask, trade). Quantities and counts have separate scales.
GROUPS = ((10, 16, 4), (11, 17, 5))


def coordinates(logs, valid):
    """Return 18 coordinates and their supervision masks.

Price geometry retains the previous absolute-close/body/wick representation.
For each activity triple, store r at the trade slot, bid-r at the bid slot,
and ask-r at the ask slot. The omitted trade deviation is -d_bid-d_ask.

With missing fields, r is the valid-member mean and missing logs are filled
with r. Thus all three completed deviations still sum to zero. All coordinates
of a nonempty triple are supervised; raw reconstruction still uses SOURCE masks.
An entirely absent triple has zero coordinates and no coordinate supervision.
"""
    code, mask = price_coordinates(logs, valid)
    code, mask = code.clone(), mask.clone()
    for bid, ask, trade in GROUPS:
        indices = (bid, ask, trade)
        observed = valid[..., list(indices)]
        values = torch.where(observed, logs[..., list(indices)], 0)
        count = observed.sum(-1)
        reference = values.sum(-1) / count.clamp_min(1)
        completed = torch.where(observed, values, reference.unsqueeze(-1))
        code[..., trade] = reference
        code[..., bid] = completed[..., 0] - reference
        code[..., ask] = completed[..., 1] - reference
        mask[..., list(indices)] = (count > 0).unsqueeze(-1)
    return code, mask


def decode(code):
    """Recover all 18 logs using ONLY predicted coordinates.

No true reference, source mask, target statistic, clipping, or quote-order
projection is used. Consequently invalid model predictions remain measurable.
The algebra also preserves unusual source-valid OHLC/quote configurations.
"""
    logs = code.clone()
    for base in (0, 6, 12):
        close, body, upper, lower = code[..., base:base + 4].unbind(-1)
        opened = close + body
        logs[..., base] = opened
        logs[..., base + 1] = torch.maximum(opened, close) + upper
        logs[..., base + 2] = torch.minimum(opened, close) - lower
        logs[..., base + 3] = close
    for bid, ask, trade in GROUPS:
        reference = code[..., trade]
        logs[..., bid] = reference + code[..., bid]
        logs[..., ask] = reference + code[..., ask]
        logs[..., trade] = reference - code[..., bid] - code[..., ask]
    return logs


def raw_values(logs):
    """Invert logs in float64 for reporting; do not hide negative/nonfinite output."""
    logs = logs.double()
    return torch.stack([
        torch.expm1(logs[..., j]) if j % 6 >= 4 else torch.exp(logs[..., j])
        for j in range(18)
    ], -1)
