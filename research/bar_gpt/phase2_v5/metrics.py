"""Streaming per-horizon price/volume metrics and causal forecast controls."""
import torch
from ..phase0_v7.transforms import raw_values
from .model import HORIZONS, NAMES, SLOTS
from .move_diagnostics import MoveMetrics

MOVE_GROUPS = ("down_large", "down_small", "flat", "up_small", "up_large")
PRICE_BANDS = ("lt1", "1_5", "5_20", "20_100", "ge100")


class Metrics:
    def __init__(self, device, *, fine_moves=False):
        self.fine_moves = MoveMetrics(device) if fine_moves else None
        self.raw = torch.zeros((2, SLOTS, 18), dtype=torch.float64, device=device)
        self.directions = torch.zeros((3, SLOTS, 3, 9), dtype=torch.int64, device=device)
        self.persistence = torch.zeros((3, SLOTS, 3), dtype=torch.float64, device=device)
        self.invalid = torch.zeros((), device=device, dtype=torch.int64)
        self.move_edges = torch.tensor([.05,.1,.25,.5,1.,2.],device=device,dtype=torch.float64)
        self.move_bins = torch.zeros((4,SLOTS,3,7),device=device,dtype=torch.float64)
        self.indices = torch.tensor([3, 9, 15], device=device)
        self.price_edges = torch.tensor([1., 5., 20., 100.], device=device)
        self.signed_moves = torch.zeros((4, SLOTS, 5), dtype=torch.float64, device=device)
        self.price_groups = torch.zeros((4, SLOTS, 15), dtype=torch.float64, device=device)

    def reset(self):
        """Keep CUDA graph buffer addresses stable between validation cycles."""
        for tensor in (self.raw, self.directions, self.persistence, self.invalid, self.move_bins,
                       self.signed_moves, self.price_groups):
            tensor.zero_()
        if self.fine_moves:
            self.fine_moves.reset()

    @torch.no_grad()
    def add(self, out, valid, evidence):
        raw, anchor, anchor_valid = evidence
        predicted = raw_values(out["logs"])
        finite = predicted.isfinite()
        self.invalid += (valid & ~finite).sum()
        keep = valid & finite
        difference = torch.where(keep, (predicted - raw).abs(), 0)
        self.raw += torch.stack((difference.sum(0), keep.sum(0)))
        p_close, y_close, close_ok = (x.index_select(-1, self.indices) for x in (predicted, raw, keep))
        opens = self.indices - 3
        base = close_ok & anchor_valid & (anchor > 0)
        self.persistence += torch.stack(((torch.where(base, (y_close - anchor).abs(), 0)).sum(0),
                                         torch.where(base, (y_close - p_close).abs(), 0).sum(0),
                                         base.sum(0)))
        move = torch.where(base,100*(y_close-anchor).abs()/anchor.clamp_min(1e-12),0)
        bins = torch.bucketize(move.contiguous(),self.move_edges).permute(1,2,0)
        correct = (p_close-anchor).sign() == (y_close-anchor).sign()
        fields = (base, base & correct, torch.where(base,(y_close-p_close).abs(),0),
                  torch.where(base,(y_close-anchor).abs(),0))
        self.move_bins.scatter_add_(-1,bins.unsqueeze(0).expand(4,-1,-1,-1),
                                    torch.stack(fields).permute(0,2,3,1).double())
        # Group trade forecasts using realized move size, or last price at
        # forecast time. Keep counts by actual sign for balanced accuracy.
        sign = torch.where(base[..., 0], y_close[..., 0] - anchor[..., 0], 0).sign().long()
        move_group = 2 + sign * (1 + (move[..., 0] > .5).long())
        price_group = torch.bucketize(anchor[..., 0].contiguous(), self.price_edges, right=True)
        trade_fields = torch.stack([field[..., 0].T for field in fields]).double()
        self.signed_moves.scatter_add_(-1, move_group.T.expand_as(trade_fields), trade_fields)
        price_index = (price_group * 3 + sign + 1).T
        self.price_groups.scatter_add_(-1, price_index.expand_as(trade_fields), trade_fields)
        if self.fine_moves:
            self.fine_moves.add(p_close[..., 0], y_close[..., 0], anchor[..., 0], base[..., 0])
        pairs = torch.zeros_like(close_ok)
        start = 0
        for width in HORIZONS:
            pairs[:, start+1:start+width] = close_ok[:, start+1:start+width] & close_ok[:, start:start+width-1]
            start += width
        tests = (
            (p_close - predicted.index_select(-1, opens), y_close - raw.index_select(-1, opens),
             close_ok & keep.index_select(-1, opens)),
            (p_close - anchor, y_close - anchor, base),
            (p_close - p_close.roll(1, 1), y_close - y_close.roll(1, 1), pairs),
        )
        for j, (forecast, target, mask) in enumerate(tests):
            label = (torch.where(mask, target, 0).sign().long()+1)*3 + torch.where(mask, forecast, 0).sign().long()+1
            self.directions[j].scatter_add_(-1, label.permute(1, 2, 0), mask.permute(1, 2, 0).long())

    def result(self):
        sums, counts = self.raw.cpu()
        move_bins = self.move_bins.cpu()
        matrices = self.directions.cpu().reshape(3, SLOTS, 3, 3, 3)
        baseline, comparable, eligible = self.persistence.cpu()
        signed_moves = self.signed_moves.cpu().tolist()
        price_groups = self.price_groups.cpu().reshape(4, SLOTS, 5, 3)
        features = ("open","high","low","close","size","count")
        streams = ("trade", "bid", "ask")
        result = {"nonfinite": int(self.invalid)}
        slot = 0
        for frame, width in zip(NAMES, HORIZONS):
            for horizon in range(1, width+1):
                prefix = f"{frame}/h{horizon:02d}"
                def group_metrics(key, size, hits, error, naive):
                    result[f"{key}/count"] = int(size)
                    if size:
                        result[f"{key}/direction_accuracy_pct"] = 100 * hits / size
                        result[f"{key}/close_mae"] = error / size
                        result[f"{key}/persistence_close_mae"] = naive / size

                for i, label in enumerate(MOVE_GROUPS):
                    group_metrics(f"{prefix}/trade/signed_move/{label}", *(field[slot][i] for field in signed_moves))
                for i, label in enumerate(PRICE_BANDS):
                    group = price_groups[:, slot, i]
                    key = f"{prefix}/trade/price_band/{label}"
                    size, hits, error, naive = group.sum(-1).tolist()
                    group_metrics(key, size, hits, error, naive)
                    if size:
                        present = group[0] > 0
                        result[f"{key}/balanced_accuracy_pct"] = float(100 * (group[1, present] / group[0, present]).mean())
                for channel in range(18):
                    n = int(counts[slot, channel])
                    if n:
                        name = streams[channel//6] + "_" + features[channel%6]
                        result[f"{prefix}/{name}/mae"] = float(sums[slot, channel]/n)
                        result[f"{prefix}/{name}/count"] = n
                for group, stream in enumerate(streams):
                    n = int(eligible[slot,group])
                    if n:
                        result[f"{prefix}/{stream}/persistence_close_mae"] = float(baseline[slot,group]/n)
                        result[f"{prefix}/{stream}/comparable_close_mae"] = float(comparable[slot,group]/n)
                    for bin_index, label in enumerate(("le_005","005_01","01_025","025_05","05_1","1_2","gt_2")):
                        size, correct, error, naive = move_bins[:,slot,group,bin_index].tolist()
                        if size:
                            key = f"{prefix}/{stream}/abs_move_pct/{label}"
                            result[f"{key}/count"] = int(size)
                            result[f"{key}/direction_accuracy_pct"] = 100*correct/size
                            result[f"{key}/close_mae"] = error/size
                            result[f"{key}/persistence_close_mae"] = naive/size
                    for mode, confusion in zip(("candle","from_last","path"), matrices[:,slot,group]):
                        subtotal = confusion.sum(1)
                        if subtotal.sum() == 0:
                            continue
                        correct = confusion.diagonal()
                        present = subtotal > 0
                        key = f"{prefix}/{stream}/{mode}"
                        result[f"{key}/accuracy_pct"] = float(100*correct.sum()/subtotal.sum())
                        result[f"{key}/balanced_accuracy_pct"] = float(100*(correct[present]/subtotal[present]).mean())
                        for index, label in enumerate(("down","flat","up")):
                            result[f"{key}/{label}_count"] = int(subtotal[index])
                slot += 1
        if self.fine_moves:
            result.update(self.fine_moves.result())
        return result
