"""Raw-price relative reconstruction errors; bounded deterministic quantiles."""

import numpy as np

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
)


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
        e, y, mask = (take(x)[:, PRICES] for x in (errors, targets, valid))
        f = take(frames).astype(np.int64)
        eligible = mask & np.isfinite(y) & (y > 0)
        with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
            ape = 100 * e / y
        for frame in np.unique(f):
            rows = f == frame
            ok, values, actual = eligible[rows], ape[rows], y[rows]
            self.excluded[frame] += (mask[rows] & ~ok).sum(axis=(0, 2))
            h = np.broadcast_to(np.arange(12)[None, :, None], ok.shape)[ok]
            values, actual = values[ok], actual[ok]
            band = np.searchsorted([1., 5., 20., 100.], actual, side='right')
            groups = h * 5 + band
            finite = np.isfinite(values)
            def merge_bands(target, values):
                target[frame,:,1:] += values
                target[frame,:,0] += values.sum(axis=1)
            merge_bands(self.counts, np.bincount(groups, minlength=60).reshape(12, 5))
            merge_bands(self.bad, np.bincount(groups[~finite], minlength=60).reshape(12, 5))
            groups, values = groups[finite], values[finite]
            merge_bands(self.sums, np.bincount(groups, weights=values, minlength=60).reshape(12, 5))
            with np.errstate(divide='ignore'):
                scaled = np.clip((np.log10(values)+9)*(8192/18), -1, 8192)
            bins = np.floor(scaled).astype(np.int64) + 2
            bins[values == 0] = 0
            merge_bands(self.hist, np.bincount(groups * NBINS + bins, minlength=60 * NBINS).reshape(12, 5, NBINS))

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
