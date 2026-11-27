"""Shared signed-move and starting-price buckets for forecast diagnostics."""
import math
import json

import numpy as np
import torch

from .model import HORIZONS, NAMES, SLOTS
from ..v5.shards.contract import digest, write_json

MOVE_EDGES = (.01, .05, .10, .25, .50, 1., 2.)
PRICE_EDGES = (1., 5., 20., 100.)
CENTER = len(MOVE_EDGES)
MOVES = 2 * CENTER + 1
PRICES = len(PRICE_EDGES) + 1
SLOT_NAMES = tuple(f"{frame}/h{j:02d}" for frame, width in zip(NAMES, HORIZONS)
                   for j in range(1, width + 1))
GROUPING = dict(version=1, move_edges_pct=MOVE_EDGES, price_edges=PRICE_EDGES,
                anchor="last valid trade close inside causal 64-bar context", slots=SLOT_NAMES)
MOVE_LABELS = ("<−2%", "−2 to −1%", "−1 to −0.5%", "−0.5 to −0.25%", "−0.25 to −0.10%",
               "−0.10 to −0.05%", "−0.05 to −0.01%", "±0.01%",
               "+0.01 to +0.05%", "+0.05 to +0.10%", "+0.10 to +0.25%", "+0.25 to +0.5%",
               "+0.5 to +1%", "+1 to +2%", ">+2%")


def session_target_counts(session):
    """Count already-loaded eligible origins using the forecast metric's anchor rules."""
    counts = np.zeros((SLOTS, PRICES, MOVES), dtype=np.int64)
    exact = np.zeros((SLOTS, PRICES), dtype=np.int64)
    if not len(session.origins):
        return counts, exact
    slot = 0
    for frame, (pool, targets) in enumerate(zip(session.pools, session.targets)):
        close = pool["values"][:, 3].astype(np.float64)
        valid = pool["valid"][:, 3].astype(bool)
        ends = session.endpoints[:, frame]
        last = np.maximum.accumulate(np.where(valid, np.arange(len(close)), -1))[ends]
        anchor = close[last.clip(0)]
        available = (last >= ends - 63) & (anchor > 0)
        for indices in targets.T:
            safe = indices.clip(0)
            keep = available & (indices >= 0) & valid[safe]
            base, actual = anchor[keep], close[safe[keep]]
            move = 100 * (actual - base) / base.clip(1e-12)
            band = np.searchsorted(PRICE_EDGES, base, side="right")
            group = CENTER + np.sign(move).astype(np.int64) * np.searchsorted(MOVE_EDGES, abs(move))
            counts[slot] = np.bincount(band * MOVES + group, minlength=PRICES * MOVES).reshape(PRICES, MOVES)
            exact[slot] = np.bincount(band[actual == base], minlength=PRICES)
            slot += 1
    return counts, exact


class TrainingCensus:
    """Track only fully committed sessions so a partial-session resume is exact."""

    def __init__(self, output, release_hash, pass_index, saved, cursor):
        self.path = output / "training-target-distribution.json"
        self.identity = dict(release=release_hash, grouping_hash=digest(GROUPING))
        self.pass_index = pass_index
        if self.path.exists():
            published = json.loads(self.path.read_text())
            if any(published.get(k) != v for k, v in self.identity.items()):
                raise ValueError("Training census identity differs")
            self.active = False
            return
        self.active = saved is not None or cursor == (0, 0)
        if saved is not None:
            if saved["identity"] != self.identity or saved["pass_index"] != pass_index or saved["sessions"] != cursor[0]:
                raise ValueError("Training census checkpoint differs from committed traversal")
            self.sessions, self.origins = saved["sessions"], saved["origins"]
            self.counts = np.asarray(saved["counts"], dtype=np.int64)
            self.exact = np.asarray(saved["exact"], dtype=np.int64)
        else:
            self.sessions = self.origins = 0
            self.counts = np.zeros((SLOTS, PRICES, MOVES), dtype=np.int64)
            self.exact = np.zeros((SLOTS, PRICES), dtype=np.int64)

    def commit(self, index, session):
        if index != self.sessions:
            raise ValueError("Training census received an out-of-order session")
        counts, exact = session.diagnostic_counts
        self.counts += counts
        self.exact += exact
        self.sessions += 1
        self.origins += len(session.origins)

    def state(self):
        return dict(identity=self.identity, pass_index=self.pass_index, sessions=self.sessions,
                    origins=self.origins, counts=self.counts.tolist(), exact=self.exact.tolist())

    def publish(self, sessions, origins):
        if (self.sessions, self.origins) != (sessions, origins):
            raise ValueError("Training census differs from certified split coverage")
        write_json(self.path, dict(**self.identity, data_pass=self.pass_index+1, grouping=GROUPING,
                                   sessions=sessions, origins=origins, counts=self.counts.tolist(),
                                   exact_flat=self.exact.tolist()))


class MoveMetrics:
    """Fixed-size, graph-capture-safe reductions for trade closes."""
    stripes = 32
    error_bins = 97

    def __init__(self, device):
        self.moves = torch.tensor(MOVE_EDGES, dtype=torch.float64, device=device)
        self.prices = torch.tensor(PRICE_EDGES, dtype=torch.float64, device=device)
        self.totals = torch.zeros((7, SLOTS, PRICES * MOVES * self.stripes),
                                  dtype=torch.float64, device=device)
        self.hist = torch.zeros((SLOTS, PRICES * MOVES * self.error_bins * 4),
                                dtype=torch.int64, device=device)

    def reset(self):
        self.totals.zero_()
        self.hist.zero_()

    def add(self, forecast, actual, anchor, keep):
        divisor = torch.where(keep, anchor.clamp_min(1e-12), 1)
        actual_delta = torch.where(keep, actual - anchor, 0)
        forecast_delta = torch.where(keep, forecast - anchor, 0)
        move = 100 * actual_delta / divisor
        groups = CENTER + move.sign().long() * torch.bucketize(move.abs().contiguous(), self.moves)
        bands = torch.bucketize(torch.where(keep, anchor, 1).contiguous(), self.prices, right=True)
        group = (bands * MOVES + groups).T.contiguous()
        forecast_move = 100 * forecast_delta / divisor
        predicted_flat = forecast_move.abs() <= MOVE_EDGES[0]
        forecast_sign = torch.where(predicted_flat, 0, forecast_move.sign())
        actual_sign = (groups - CENTER).sign()
        error = torch.where(keep, forecast - actual, 0)
        absolute = error.abs()
        fields = torch.stack((keep, keep & (forecast_sign == actual_sign), absolute, actual_delta.abs(),
                              error, keep & predicted_flat, keep & (actual_delta == 0))).transpose(1, 2)
        # Stripe hot cells such as near-flat to reduce scatter contention.
        rows = torch.arange(len(forecast), device=forecast.device)
        index = group * self.stripes + rows % self.stripes
        self.totals.scatter_add_(-1, index.expand_as(fields), fields)
        # Log-spaced dollar bins give bounded-memory P90 brackets. Bin 0
        # covers tiny errors; the last bin covers overflow without clipping MAE.
        error_bin = ((absolute.clamp_min(2.**-16).log2() + 16) * 3).floor().long().clamp(0, self.error_bins-1)
        self.hist.scatter_add_(-1, (group * self.error_bins + error_bin.T) * 4 + rows % 4,
                               keep.T.long())

    def result(self):
        totals = self.totals.view(7, SLOTS, PRICES, MOVES, self.stripes).sum(-1).cpu().numpy()
        hist = self.hist.view(SLOTS, PRICES, MOVES, self.error_bins, 4).sum(-1).cpu().numpy()
        if not np.array_equal(totals[0], hist.sum(-1)):
            raise ValueError("Forecast diagnostic histogram and pair counts differ")
        result = {}
        for slot, prefix in enumerate(SLOT_NAMES):
            for band in range(-1, PRICES):
                cells = totals[:, slot].sum(1) if band == -1 else totals[:, slot, band]
                bins = hist[slot].sum(0) if band == -1 else hist[slot, band]
                for group, (n, hits, error, base, bias, flat, exact) in enumerate(cells.T):
                    key = f"{prefix}/trade/fine_move/{band}/{group}"
                    result[f"{key}/count"] = int(n)
                    result[f"{key}/exact_flat_count"] = int(exact)
                    if n:
                        selected = int(np.searchsorted(bins[group].cumsum(), math.ceil(.9 * n)))
                        result.update({f"{key}/direction_accuracy_pct": 100 * hits / n,
                                       f"{key}/close_mae": error / n,
                                       f"{key}/persistence_close_mae": base / n,
                                       f"{key}/signed_error": bias / n,
                                       f"{key}/predicted_near_flat_pct": 100 * flat / n,
                                       f"{key}/p90_lower": 0. if selected == 0 else 2.**(selected/3-16)})
        return result
