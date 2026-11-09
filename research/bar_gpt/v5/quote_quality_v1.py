"""Deterministic model-bar quarantine, shared by both pretraining phases.

This is a model-input policy, not a correction to canonical SIP or ARTE prices.
Raw source and old shards remain immutable. A rejected paired quote family is
unknown evidence: all twelve quote values AND masks become zero. Trade values,
sample clocks, origin selection and corporate-action factors are unchanged.
There is deliberately no clock-age cutoff and no forward/future reference.

The 100-fold guard is a conservative quarantine threshold, not a claim that a
wide positive-size market is necessarily erroneous. Its false positives remain
reviewable in the original payload. This policy cannot certify that every
plausible-looking source error has been detected.
"""

import numpy as np

POLICY = dict(
    version="bargpt-paired-quote-quarantine-v1",
    maximum_price_multiple=100.0,
    action="zero and mask both quote families; retain original immutable payload",
    reference="same completed bar trade low/high; invariant to common split adjustment",
    carry="unchanged session carry; no added age cutoff",
)

# Massive's QUOTE modifier namespace, never encoded token IDs or trade codes.
# Corrected_Price (81), SIPGenerated (82), locked markets (85), and slow-but-
# otherwise-valid quotes are deliberately not rejected solely by condition.
REJECTED_QUOTE_MODIFIERS = (
    -1,
    12,
    15,
    18,
    19,
    20,
    32,
    34,
    35,
    36,
    37,
    38,
    39,
    43,
    80,
    83,
    84,
    89,
)


def condition_sql(rules):
    """Map frozen quote-condition records to encoded tokens for SQL eligibility.

    ``rules`` is the canonical dictionary in the dataset contract. Empty token
    arrays represent unqualified messages. A nonzero unrecognized token fails
    closed. Trade-condition IDs never enter this mapping. Reference:
    https://massive.com/glossary/conditions-indicators (Quote Conditions).
    """
    quotes = [r for r in rules if r["source_family"] == "quote_conditions"]
    if not quotes:
        raise ValueError("Frozen quote-condition dictionary is required")
    known = sorted({int(r["token_id"]) for r in quotes})
    rejected = sorted(
        {
            int(r["token_id"])
            for r in quotes
            if int(r["modifier_int"]) in REJECTED_QUOTE_MODIFIERS
        }
    )
    return f"arrayAll(t->has({known},t),tokens) AND NOT hasAny(tokens,{rejected})"


def reasons(values, valid):
    """Return reason masks shaped [...], for Float32 values/masks [...,18].

    Each observation is one completed bar, optionally nested in [N,64]. Only
    explicitly present fields participate. Already masked placeholders do not
    become fake observations. Reasons overlap; counts must not be added to get
    the unique number of rejected bars. Trade corruption fails closed elsewhere.
    """
    values, valid = np.asarray(values), np.asarray(valid)
    if values.shape != valid.shape or values.shape[-1] != 18:
        raise ValueError("Quote quality requires aligned values/masks [...,18]")
    if not np.isin(valid, (0, 1)).all():
        raise ValueError("Malformed evidence mask")
    quote, mask = values[..., 6:], valid[..., 6:].astype(bool)
    result = {"nonfinite": (~np.isfinite(quote)).any(axis=-1)}
    shape = values.shape[:-1]
    geometry = np.zeros(shape, dtype=bool)
    price = np.zeros(shape, dtype=bool)
    size = np.zeros(shape, dtype=bool)
    counts = np.zeros(shape, dtype=bool)
    distant = np.zeros(shape, dtype=bool)
    limit = POLICY["maximum_price_multiple"]
    reference = valid[..., 1].astype(bool) & valid[..., 2].astype(bool)
    reference &= (values[..., 2] > 0) & (values[..., 1] >= values[..., 2])
    for offset in (6, 12):
        p, m = values[..., offset : offset + 4], valid[..., offset : offset + 4].astype(
            bool
        )
        price |= ((p <= 0) & m).any(axis=-1)
        size |= valid[..., offset + 4].astype(bool) & (values[..., offset + 4] <= 0)
        count = values[..., offset + 5]
        counts |= valid[..., offset + 5].astype(bool) & (
            (count < 0) | (count != np.floor(count))
        )
        # Compare only fields with evidence; a final invalidation may mask close
        # while earlier observed extrema remain meaningful in the source bar.
        geometry |= m[..., 1] & m[..., 2] & (p[..., 1] < p[..., 2])
        for index in (0, 3):
            geometry |= (
                m[..., index] & m[..., 1] & (p[..., index] > p[..., 1] * (1 + 2e-6))
            )
            geometry |= (
                m[..., index] & m[..., 2] & (p[..., index] < p[..., 2] * (1 - 2e-6))
            )
        # Multiplication avoids unstable division at very small stock prices.
        distant |= reference & (
            ((p > values[..., 1, None] * limit) | (p * limit < values[..., 2, None]))
            & m
        ).any(axis=-1)
    closes = valid[..., 9].astype(bool) & valid[..., 15].astype(bool)
    result.update(
        nonpositive_price=price,
        nonpositive_size=size,
        invalid_count=counts,
        ohlc_geometry=geometry,
        crossed_close=closes & (values[..., 9] > values[..., 15]),
        extreme_spread=closes & (values[..., 15] > values[..., 9] * limit),
        extreme_trade_distance=distant,
    )
    return result


def sanitize(values, valid):
    """Copy [...,18] inputs; return clean values, masks and scalar audit counts.

    Quarantine is paired because the original producer admitted paired books.
    No synthetic high, close, size or count is invented. Idempotence permits the
    same check before shard publication and at model-window assembly.
    """
    flags = reasons(values, valid)
    rejected = np.logical_or.reduce(list(flags.values()))
    clean, masks = np.array(values, copy=True), np.array(valid, copy=True)
    clean[..., 6:][rejected] = 0
    masks[..., 6:][rejected] = 0
    counts = {name: int(mask.sum()) for name, mask in flags.items()}
    counts.update(bars=int(rejected.size), quarantined=int(rejected.sum()))
    return clean, masks, counts


def neighbor_spikes(values, valid, multiple=10.0):
    """Return offline-only isolated-spike flags [...,bars] on a common share basis.

    Compare each interior quote price against BOTH adjacent observed bars of the
    same ticker/timeframe. Never use this future-dependent result as an input
    filter: condition/source reconstruction and causal gates make that decision.
    Cross-split raw arrays must first be adjusted to one basis by the caller.
    First/last bars have no two-sided neighborhood and are explicitly untested.
    """
    if values.shape != valid.shape or values.shape[-1] != 18 or values.ndim < 2:
        raise ValueError("Neighbor audit expects [...,bars,18]")
    result = np.zeros(values.shape[:-1], dtype=bool)
    if values.shape[-2] < 3:
        return result
    for column in (6, 7, 8, 9, 12, 13, 14, 15):
        prices, masks = values[..., column], valid[..., column].astype(bool)
        previous, current, following = (
            prices[..., :-2],
            prices[..., 1:-1],
            prices[..., 2:],
        )
        usable = (
            masks[..., :-2]
            & masks[..., 1:-1]
            & masks[..., 2:]
            & (previous > 0)
            & (following > 0)
            & (current > 0)
        )
        extreme = (current > multiple * np.maximum(previous, following)) | (
            current * multiple < np.minimum(previous, following)
        )
        result[..., 1:-1] |= usable & extreme
    return result
