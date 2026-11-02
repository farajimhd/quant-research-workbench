"""Versioned completed-bar context; no expanded sequences persisted on disk."""
import numpy as np
from research.market_shards.v1.contract import TRADE_WINDOWS, QUOTE_WINDOWS
from research.market_shards.v1.data import TRADE_FIELDS, QUOTE_FIELDS

EXCLUDED = frozenset(('log_float_shares', 'float_present',
                      'log_shares_outstanding', 'shares_present'))
# Oldest to newest, matching the shared loader's scheduled window order.
SPECS = tuple((resolution, side, lag, field)
    for side, windows, fields in ((None, TRADE_WINDOWS, TRADE_FIELDS),
                                  ('bid', QUOTE_WINDOWS, QUOTE_FIELDS),
                                  ('ask', QUOTE_WINDOWS, QUOTE_FIELDS))
    for resolution, count in windows.items()
    for lag in range(count - 1, -1, -1) for field in fields)


def name(spec):
    resolution, side, lag, field = spec
    return f'arte.{side or "trade"}.{resolution}ms.lag{lag}.{field}'


def encode(values, valid, fields, reference):
    """Prices relative to current mark; activity log1p; gaps remain unavailable."""
    values = np.asarray(values, dtype=np.float32).copy()
    valid = np.asarray(valid, dtype=bool).copy()
    reference = np.asarray(reference, dtype=np.float64)
    for column, field in enumerate(fields):
        if field in ('open', 'high', 'low', 'close'):
            ok = np.isfinite(reference) & (reference > 0)
            valid[..., column] &= ok[:, None] & (values[..., column] > 0)
            values[..., column] = (values[..., column].astype(np.float64) /
                np.where(ok, reference, 1.)[:, None] - 1).astype(np.float32)
        else:
            valid[..., column] &= values[..., column] >= 0
            values[..., column] = np.log1p(np.maximum(values[..., column], 0))
    valid &= np.isfinite(values)
    return np.where(valid, values, np.nan).reshape(len(values), -1)


def materialize(session, tickers, clocks, reference, batch_size=4096):
    """Bound temporary arrays; shared loader verifies immutable source hashes."""
    output = np.empty((len(clocks), len(SPECS)), dtype=np.float32)
    offset = 0
    for side, windows, fields in ((None, TRADE_WINDOWS, TRADE_FIELDS),
                                  ('bid', QUOTE_WINDOWS, QUOTE_FIELDS),
                                  ('ask', QUOTE_WINDOWS, QUOTE_FIELDS)):
        for resolution, count in windows.items():
            width = count * len(fields)
            for begin in range(0, len(clocks), batch_size):
                end = min(begin + batch_size, len(clocks))
                part = session.windows(tickers[begin:end], clocks[begin:end], resolution, side)
                output[begin:end, offset:offset+width] = encode(
                    part['values'], part['valid'], fields, reference[begin:end])
            offset += width
    return output
