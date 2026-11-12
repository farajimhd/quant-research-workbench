"""Raw-price relative reconstruction errors; bounded deterministic quantiles."""

import numpy as np
import math
from numba import njit

PRICES = (0, 1, 2, 3, 6, 7, 8, 9, 12, 13, 14, 15)
BANDS = ('below_1', '1_to_5', '5_to_20', '20_to_100', '100_and_above')
EDGES = np.geomspace(1e-9, 1e9, 8193)
NBINS = len(EDGES) + 2  # zero, underflow, finite bins, overflow
CONTRACT = dict(
    version='raw-price-relative-v3',
    formula='100 * abs(decoded_dollar_price - actual_dollar_price) / actual_dollar_price',
    population='source-valid finite strictly positive actual prices; observation weighted',
    bands='actual per-observation split-adjusted USD price: [0,1),[1,5),[5,20),[20,100),[100,infinity)',
    quantiles='nearest-rank streaming log-histogram bin midpoint; approximate, not exact order statistics',
    quantile_max_bin_relative_width=float(EDGES[1]/EDGES[0]-1),
    quantile_bounds='zero exact; outside [1e-9,1e9) percent reported unavailable',
    nonfinite_prediction='eligible observation counted as overflow; affected aggregate unavailable',
    backend='fused CPU Float64 no-GIL loop; no fastmath; JIT cache disabled',
)


@njit(nogil=True, cache=False, error_model='numpy')
def _reduce_prices(errors, targets, valid, frames, sums, counts, bad, excluded, hist):
    """Fused Float64 CPU loop; no fastmath, sampling or extra price-array copies."""
    for row in range(errors.shape[0]):
        frame = frames[row]
        for head in range(12):
            channel = PRICES[head]
            for clock in range(errors.shape[2]):
                if not valid[row, channel, clock]:
                    continue
                actual = targets[row, channel, clock]
                if not math.isfinite(actual) or actual <= 0:
                    excluded[frame, head] += 1
                    continue
                band = 1 if actual < 1 else 2 if actual < 5 else 3 if actual < 20 else 4 if actual < 100 else 5
                counts[frame, head, band] += 1
                value = 100. * errors[row, channel, clock] / actual
                if not math.isfinite(value):
                    bad[frame, head, band] += 1
                    continue
                sums[frame, head, band] += value
                if value == 0:
                    bucket = 0
                else:
                    scaled = min(8192., max(-1., (math.log10(value) + 9) * (8192 / 18)))
                    bucket = int(math.floor(scaled)) + 2
                hist[frame, head, band, bucket] += 1


class PriceRelative:
    def __init__(self):
        self.sums = np.zeros((6, 12, 6), dtype=np.float64)
        self.counts = np.zeros_like(self.sums, dtype=np.int64)
        self.bad = np.zeros_like(self.counts)
        self.excluded = np.zeros((6, 12), dtype=np.int64)
        self.hist = np.zeros((6, 12, 6, NBINS), dtype=np.int64)

    def add(self, errors, targets, valid, frames):
        # errors are already inversely converted absolute USD errors. No log-space error.
        take = lambda x: x.detach().cpu().numpy()
        e, y, mask, f = (take(x) for x in (errors, targets, valid, frames))
        if (e.ndim != 3 or e.shape[1:] != (18,64) or y.shape != e.shape
                or mask.shape != e.shape or mask.dtype != np.bool_
                or f.shape != (len(e),) or f.dtype.kind not in 'iu'
                or np.any(f < 0) or np.any(f >= 6)):
            raise ValueError('Price reduction requires aligned [B,18,64] arrays and frame indices0..5')
        _reduce_prices(e, y, mask, f,
                       self.sums, self.counts, self.bad, self.excluded, self.hist)
        # Keep the all-price aggregate observation weighted. Only small summaries
        # and the fixed histogram are reduced; no [batch,12,64] temporaries.
        for array in (self.sums, self.counts, self.bad, self.hist):
            array[:, :, 0] = array[:, :, 1:].sum(axis=2)

    def merge(self, other):
        for name in ('sums', 'counts', 'bad', 'excluded', 'hist'):
            getattr(self, name)[:] += getattr(other, name)

    @staticmethod
    def quantile(hist, n, q):
        index = int(np.searchsorted(np.cumsum(hist), max(1, int(np.ceil(q*n)))))
        if index == 0:
            return 0.
        if index == 1 or index >= len(EDGES)+1:
            return None
        return float(np.sqrt(EDGES[index-2]*EDGES[index-1]))

    def metrics(self, raw_prefix, frame_names, channels):
        stem = raw_prefix.removesuffix('raw_mae') + 'price_'
        result = {}
        for f, frame in enumerate(frame_names):
            for h, channel in enumerate(PRICES):
                tail = f'{frame}/{channels[channel]}'
                result[f'{stem}relative_excluded/{tail}'] = int(self.excluded[f,h])
                for band in range(6):
                    suffix = '' if band == 0 else '_by_price_band'
                    path = tail if band == 0 else f'{BANDS[band-1]}/{tail}'
                    n, bad, total = int(self.counts[f,h,band]), int(self.bad[f,h,band]), self.sums[f,h,band]
                    usable = n > 0 and not bad and np.isfinite(total)
                    for label, value in (
                        ('mape_pct', float(total/n) if usable else None),
                        ('median_ape_pct', self.quantile(self.hist[f,h,band], n, .5) if usable else None),
                        ('p90_ape_pct', self.quantile(self.hist[f,h,band], n, .9) if usable else None),
                    ):
                        result[f'{stem}{label}{suffix}/{path}'] = value
                    result[f'{stem}relative_counts{suffix}/{path}'] = n
                    result[f'{stem}relative_overflows{suffix}/{path}'] = bad
        result[f'{stem}relative_quantile_bin_relative_width'] = CONTRACT['quantile_max_bin_relative_width']
        return result
