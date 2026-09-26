"""Teacher-independent, immutable full-population market sessions."""
from datetime import date
from pathlib import Path
import numpy as np

from research.rl_trading.v1.common import bounds, digest, file_hash
from research.rl_trading.v2.io import read

DATA_VERSION = 'rl-trading-v2-market-1'
ARRAYS = ('features', 'prices', 'volume', 'volume_60s', 'trades_60s', 'fresh')


class MarketSession:
    def __init__(self, plan, arrays, root=None):
        self.plan, self.arrays, self.root = plan, arrays, root
        self.ids = tuple(str(x['listing_id']) for x in plan['listings'])
        self.tickers = tuple(x['ticker'] for x in plan['listings'])
        self.n, self.seconds = arrays['prices'].shape
        if (not self.n or self.seconds < 3 or len(self.ids) != self.n
                or len(set(self.ids)) != self.n or len(set(self.tickers)) != self.n
                or arrays['features'].shape != (self.n, self.seconds, len(plan['feature_names']))):
            raise ValueError('Market session identity/shape mismatch')
        if plan.get('clock') != 'completed_second' or plan.get('step_us') != 1000000:
            raise ValueError('Require causal one-second market observations')
        for name in ARRAYS:
            a = arrays[name]
            if name != 'features' and a.shape != (self.n, self.seconds):
                raise ValueError('Invalid market array shape: ' + name)
            for start in range(0, self.n, 16):
                chunk = a[start:start+16]
                if not np.isfinite(chunk).all() or (name != 'features' and np.any(chunk < 0)):
                    raise ValueError('Invalid market values: ' + name)
                if name == 'fresh' and np.any(chunk & (arrays['prices'][start:start+16] <= 0)):
                    raise ValueError('Fresh observations require a positive price')
        if arrays['fresh'].dtype != np.bool_:
            raise ValueError('Fresh price contract is invalid')
        self.lexical = np.argsort(np.asarray(self.ids), kind='stable')

    @classmethod
    def load(cls, root, *, allow_segment=False):
        root = Path(root).resolve()
        plan, complete = read(root/'plan.json'), read(root/'complete.json')
        if (plan.get('version') != DATA_VERSION
                or plan.get('teacher_dependency') is not False
                or plan.get('plan_hash') != digest({k:v for k,v in plan.items() if k != 'plan_hash'})
                or complete.get('plan_hash') != plan['plan_hash']
                or set(complete['files']) != {x+'.npy' for x in ARRAYS}
                or complete.get('listing_count') != len(plan['listings'])):
            raise ValueError('Invalid V2 market certificate')
        if plan['segment'] and not allow_segment:
            raise ValueError('Segment data requires explicit --allow-segment')
        if not plan['segment']:
            left, right = bounds(date.fromisoformat(plan['date']))
            if plan['first_us'] != left or plan['rows'] != (right-left)//1000000+1:
                raise ValueError('Full session must cover 04:00 through 20:00 ET')
        arrays = {}
        for name in ARRAYS:
            path = root/(name+'.npy')
            if file_hash(path) != complete['files'][path.name]:
                raise ValueError('Market integrity failure: ' + name)
            arrays[name] = np.load(path, mmap_mode='r', allow_pickle=False)
        if arrays['prices'].shape[1] != plan['rows']:
            raise ValueError('Market row certificate mismatch')
        return cls(plan, arrays, root)

    def ranking(self, second, config):
        a = self.arrays
        # A short bounded scan avoids storing an additional full-population age bank.
        observed = a['fresh'][:,second].copy()
        for lag in range(1, min(second, config.max_price_age_seconds)+1):
            observed |= a['fresh'][:,second-lag]
        eligible = (observed & (a['prices'][:,second] > 0)
                    & (a['volume_60s'][:,second] >= config.min_volume_60s)
                    & (a['trades_60s'][:,second] >= config.min_trades_60s))
        indices = self.lexical[eligible[self.lexical]]
        return indices[np.argsort(-a['volume_60s'][indices,second], kind='stable')]


def chronological(train, validation):
    first = [x.plan['date'] for x in train]
    second = [x.plan['date'] for x in validation]
    if (not first or not second or len(set(first)) != len(first)
            or len(set(second)) != len(second) or max(first) >= min(second)):
        raise ValueError('Require unique training dates strictly before validation dates')
    if any(x.plan['feature_names'] != train[0].plan['feature_names'] for x in train+validation):
        raise ValueError('Feature schemas differ across sessions')
