"""Compact certified feature banks; no expanded feature-tile I/O on GPU.

The elapsed-second -> source/history maps remain authoritative. Float32 history
rounding and float64 relative-price arithmetic match data.prepare_feature_block.
Only the requested channels are gathered; this does not change their clocks.
"""
import numpy as np
import torch
from .features import BASE, HISTORY, PRICE_COLUMNS, ORIGINAL as CATALOG
from research.vectorized_backtest.v60.input_constants import KEY_STRIDE


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
        # CUDA may replace scalar division by reciprocal multiplication.
        # At epoch-scale timestamps this changes quote age by a float64 ULP.
        arrays['quote_seconds']=arrays['quote_us']/1e6
        # Preserve the existing NumPy log1p rounding once per source row.
        for name in ('volume', 'notional', 'trade_count'):
            arrays['log_' + name] = np.log1p(data.market[name]).astype(np.float32)
        # SessionData already owns the current-clock swing projection. Do not
        # construct or transfer a second, differently gated history bank here.
        self.host = {k: torch.from_numpy(np.array(v, copy=True)) for k, v in arrays.items()}
        self.bytes = sum(v.numel() * v.element_size() for v in self.host.values())
        self.tensors = self.host
        self.device = torch.device('cpu')
        self.column_plans = {}

    def pin(self):
        self.host = {k: v if v.is_pinned() else v.pin_memory() for k, v in self.host.items()}

    def activate(self, device):
        self.device = torch.device(device)
        self.tensors = {k: v.to(device, non_blocking=True) for k, v in self.host.items()}
        self.column_plans.clear()

    def deactivate(self):
        self.device = torch.device('cpu')
        self.tensors = self.host
        self.column_plans.clear()

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
        key=tuple(columns)
        if key not in self.column_plans:
            plans=[];order=[]
            for family in range(4):
                selected=[(i,c) for i,c in enumerate(columns)
                          if (0 if c<len(BASE) else 1 if c<len(BASE)+len(HISTORY)
                              else 2 if c<len(CATALOG)-11 else 3)==family]
                if not selected:continue
                order.extend(i for i,_ in selected)
                indices=[c if family==0 else c-len(BASE) if family==1
                         else PRICE_COLUMNS[c-len(BASE)-len(HISTORY)] if family==2
                         else c for _,c in selected]
                plans.append((family,selected,torch.tensor(indices,device=self.device)))
            self.column_plans[key]=(plans,torch.tensor(np.argsort(order),device=self.device))
        plans,inverse=self.column_plans[key]
        for family,selected,indices in plans:
            if family == 0:
                v = a['base'][feature_row.clamp_min(0)[..., None], indices]
                m = a['base_valid'][feature_row.clamp_min(0)[..., None], indices]
                m = m & known[..., None] & (feature_row >= 0)[..., None]
            elif family == 1:
                v = a['history'][ids[..., None], indices]
                m = torch.isfinite(v) & known[..., None]
            elif family == 2:
                h = a['history'][ids[..., None], indices]
                v = (h.to(torch.float64)/price[..., None]-1).to(torch.float32)
                m = torch.isfinite(v) & known[..., None] & (price > 0)[..., None]
            else:
                bid, ask, quote = (a[n][safe] for n in ('bid', 'ask', 'quote_us'))
                age = clock-a['quote_seconds'][safe]
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
        values, valid = values.index_select(-1, inverse), valid.index_select(-1, inverse)
        return torch.where(valid, values, 0.), valid
