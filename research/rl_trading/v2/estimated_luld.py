"""Causal research proxy for regular-session LULD bands, not official SIP bands."""
import numpy as np

# Seconds are completed bars measured from 04:00 ET. The first regular bar
# completes at 09:30:01 and the final one at 16:00:00.
REGULAR_FIRST = 19_801
REGULAR_LAST = 43_200
WINDOW_SECONDS = 300
PRIOR_CLOSE_MINIMUM = .75
# Five percent is deliberately narrower than many official bands. It is a
# research risk geometry, not a reconstruction of the SIP's tiered band rule.
ESTIMATED_BAND_RATIO = .05
BRACKET_BUFFER_RATIO = .005


def regular(second):
    return REGULAR_FIRST <= second <= REGULAR_LAST


def reference_series(prices, volume, fresh, prior_close):
    """Prior close until a regular print, then trailing five-minute volume mean.

    Bars contain aggregates rather than eligible individual transactions; this
    is explicitly an estimate. Only completed regular-session bars enter it.
    """
    prices = np.asarray(prices, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)
    fresh = np.asarray(fresh, dtype=bool)
    if prices.ndim != 1 or volume.shape != prices.shape or fresh.shape != prices.shape:
        raise ValueError('Estimated LULD inputs have different shapes')
    if not np.isfinite(prior_close) or prior_close < 0:
        raise ValueError('Invalid certified prior close')
    result = np.full(prices.shape, prior_close, dtype=np.float32)
    if len(prices) <= REGULAR_FIRST:
        return result
    eligible = np.zeros(len(prices), dtype=bool)
    eligible[REGULAR_FIRST:min(len(prices), REGULAR_LAST + 1)] = True
    eligible &= fresh & (volume > 0) & (prices > 0)
    weighted = np.where(eligible, prices * volume, 0.)
    weights = np.where(eligible, volume, 0.)
    weighted_sum = np.concatenate(([0.], np.cumsum(weighted)))
    weight_sum = np.concatenate(([0.], np.cumsum(weights)))
    end = np.arange(len(prices)) + 1
    start = np.maximum(0, end - WINDOW_SECONDS)
    numerator = weighted_sum[end] - weighted_sum[start]
    denominator = weight_sum[end] - weight_sum[start]
    valid = denominator > 0
    result[valid] = (numerator[valid] / denominator[valid]).astype(np.float32)
    # Once the regular session closes, the last estimate is retained for
    # diagnostics, but regular-only admission never reads it after 16:00 ET.
    if len(result) > REGULAR_LAST + 1:
        result[REGULAR_LAST + 1:] = result[REGULAR_LAST]
    return result


def buffered_bounds(reference):
    if not np.isfinite(reference) or reference <= 0:
        return None
    return (reference * (1 - ESTIMATED_BAND_RATIO + BRACKET_BUFFER_RATIO),
            reference * (1 + ESTIMATED_BAND_RATIO - BRACKET_BUFFER_RATIO))
