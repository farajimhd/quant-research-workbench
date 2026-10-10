"""Compact certified feature banks; no expanded feature-tile I/O on GPU.

The elapsed-second -> source/history maps remain authoritative. Float32 history
rounding and float64 relative-price arithmetic match data.prepare_feature_block.
Only the requested channels are gathered; this does not change their clocks.
"""
import numpy as np
import torch
from .features import BASE, HISTORY, PRICE_COLUMNS, CATALOG
from research.vectorized_backtest.v6.torch_backtest.compact_prepare import KEY_STRIDE


class ResidentFeatures:
    def __init__(self, data):
        arrays = dict(
            base=np.asarray(data.arrays['features']),
            base_valid=np.asarray(data.arrays['feature_valid']),
            history=np.asarray(data.bank['values'], dtype=np.float32),
            source=np.asarray(data.bank['source_rows']),
            ids=np.asarray(data.bank['ids']),
            clocks=np.asarray(data.arrays['clocks']),
            market_clock=np.asarray(data.arrays['market_keys']) % KEY_STRIDE,
            feature_row=np.asarray(data.market['feature_row'], dtype=np.int64),
        )
        for name in ('mark', 'bid', 'ask', 'quote_us'):
            arrays[name] = np.asarray(data.market[name], dtype=np.float64)
        # Preserve the existing NumPy log1p rounding once per source row.
        for name in ('volume', 'notional', 'trade_count'):
            arrays['log_' + name] = np.log1p(data.market[name]).astype(np.float32)
        self.host = {k: torch.from_numpy(np.array(v, copy=True)) for k, v in arrays.items()}
        self.bytes = sum(v.numel() * v.element_size() for v in self.host.values())
        self.tensors = self.host
        self.device = torch.device('cpu')

    def pin(self):
        self.host = {k: v if v.is_pinned() else v.pin_memory() for k, v in self.host.items()}

    def activate(self, device):
        self.device = torch.device(device)
        self.tensors = {k: v.to(device, non_blocking=True) for k, v in self.host.items()}

    def deactivate(self):
        self.device = torch.device('cpu')
        self.tensors = self.host

    def block(self, begin, end, listings, columns):
        """Return [listing, elapsed second, requested feature], including masks."""
        a = self.tensors
        listing = torch.as_tensor(listings, device=self.device, dtype=torch.int64)
        rows = a['source'][begin:end].index_select(1, listing).T
        ids = a['ids'][begin:end].index_select(1, listing).T.to(torch.int64)
        clock = a['clocks'][begin:end][None]
        return self._values(rows, ids, clock, columns)

    def points(self, clocks, listings, columns):
        a = self.tensors
        clocks = torch.as_tensor(clocks, device=self.device, dtype=torch.int64)
        listings = torch.as_tensor(listings, device=self.device, dtype=torch.int64)
        rows = a['source'][clocks, listings][None]
        ids = a['ids'][clocks, listings][None].to(torch.int64)
        return self._values(rows, ids, a['clocks'][clocks][None], columns)

    def _values(self, rows, ids, clock, columns):
        a = self.tensors
        known = rows >= 0
        safe = rows.clamp_min(0)
        feature_row = a['feature_row'][safe]
        price = a['mark'][safe]
        groups = []
        masks = []
        order = []
        for family in range(4):
            selected = [(i, c) for i, c in enumerate(columns)
                        if (0 if c < len(BASE) else 1 if c < len(BASE)+len(HISTORY)
                            else 2 if c < len(CATALOG)-11 else 3) == family]
            if not selected:
                continue
            order.extend(i for i, _ in selected)
            indices = torch.tensor([c for _, c in selected], device=self.device)
            if family == 0:
                v = a['base'][feature_row.clamp_min(0)[..., None], indices]
                m = a['base_valid'][feature_row.clamp_min(0)[..., None], indices]
                m = m & known[..., None] & (feature_row >= 0)[..., None]
            elif family == 1:
                v = a['history'][ids[..., None], indices-len(BASE)]
                m = torch.isfinite(v) & known[..., None]
            elif family == 2:
                source_columns = torch.tensor([PRICE_COLUMNS[c-len(BASE)-len(HISTORY)]
                                               for _, c in selected], device=self.device)
                h = a['history'][ids[..., None], source_columns]
                v = (h.to(torch.float64)/price[..., None]-1).to(torch.float32)
                m = torch.isfinite(v) & known[..., None] & (price > 0)[..., None]
            else:
                bid, ask, quote = (a[n][safe] for n in ('bid', 'ask', 'quote_us'))
                age = clock-quote/1e6
                quote_valid = known & (quote > 0) & (age >= 0) & (age <= 1) & (bid > 0) & (ask >= bid)
                current = known & (a['market_clock'][safe] == clock)
                all_values = (bid/price-1, ask/price-1, (ask-bid)/price, age,
                              *(torch.where(current, a['log_'+n][safe], 0.)
                                for n in ('volume', 'notional', 'trade_count')),
                              bid, ask, ask-bid, price)
                values, valid = [], []
                for _, column in selected:
                    local = column-(len(CATALOG)-11)
                    value = all_values[local]
                    mask = torch.isfinite(value) & known
                    if local in (0, 1, 2, 7, 8, 9): mask = mask & quote_valid
                    elif local == 3: mask = mask & (quote > 0) & (age >= 0)
                    elif local == 10: mask = mask & (price > 0)
                    value=value.to(torch.float32)
                    values.append(value); valid.append(mask & torch.isfinite(value))
                v, m = torch.stack(values, -1), torch.stack(valid, -1)
            groups.append(v); masks.append(m)
        values, valid = torch.cat(groups, -1), torch.cat(masks, -1)
        inverse = torch.tensor(np.argsort(order), device=self.device)
        values, valid = values.index_select(-1, inverse), valid.index_select(-1, inverse)
        return torch.where(valid, values, 0.), valid
