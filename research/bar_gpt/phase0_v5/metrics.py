"""Bounded, vectorized same-input reconstruction diagnostics.

Adjacent-bar metrics compare two independently reconstructed bars. They do NOT
give the model temporal context and are not forecasting or trading accuracy.
All reductions keep original source-valid masks and report excluded denominators.
"""

import numpy as np
import torch
from torch.nn import functional as F

from ..phase1_v11.diagnostic_metrics import Diagnostics
from ..phase0_v1.metrics import scalars as price_scalars
from ..v5.shards.phase1_contract import FRAMES, CHANNELS
from ..phase1_v5_raw.engine import FRAME_NAMES
from .objectives import objective
from .transforms import raw_values, coordinates

ACTIVITY = (4, 5, 10, 11, 16, 17)


class Metrics:
    """Accumulate sufficient statistics on-device, not whole predictions in RAM."""

    def __init__(self, device="cpu"):
        self.windows = 0
        self.loss = torch.zeros((), device=device, dtype=torch.float64)
        # Six legacy fields allow reuse of the existing candle/range report.
        self.raw = torch.zeros(6, 18, 6, device=device, dtype=torch.float64)
        # count, target sum/squares, prediction sum/squares, log absolute error.
        self.moments = torch.zeros(6, 18, 6, device=device, dtype=torch.float64)
        self.moves = Diagnostics(device)
        self.activity_confusion = torch.zeros(6 * 6 * 9, device=device, dtype=torch.float64)
        self.violations = torch.zeros(8, device=device, dtype=torch.float64)
        self.coordinate_errors = torch.zeros(6, 18, 2, device=device, dtype=torch.float64)
        self.embedding_moments = None
        self.embedding_count = 0
        self.denominators = torch.zeros(6, 18, 2, device=device, dtype=torch.float64)
        # Fixed histograms make median/P90 memory independent of population size.
        # Report bucket UPPER bounds, not invented exact quantiles.
        self.edges = torch.cat(
            (torch.zeros(1, device=device), torch.logspace(-6, 6, 241, device=device))
        ).double()
        self.histogram = torch.zeros(
            6 * 18 * (len(self.edges) + 1), device=device, dtype=torch.float64
        )
        self.band_edges = torch.tensor([0.1, 1.0, 5.0, 20.0, 100.0], device=device)
        self.bands = torch.zeros(6 * 6, 3, device=device, dtype=torch.float64)
        # Create constants outside graph capture; constructing them from Python
        # lists inside capture would require a forbidden host-to-device copy.
        self.frames = torch.tensor(FRAMES, device=device)
        self.activity_indices = torch.tensor(ACTIVITY, device=device)
        self.unknown_frames = torch.zeros((), device=device, dtype=torch.int64)
        self.embedding_count = torch.zeros((), device=device, dtype=torch.int64)

    @torch.no_grad()
    def add(self, model, out, values, valid, batch):
        """Reduce one complete batch; no CPU loop over bars or observations."""
        target = batch.raw.double()
        predicted = raw_values(out["logs"])
        source = valid[..., :18]
        finite = torch.isfinite(predicted)
        self.violations[0] += (~finite & source).sum()
        # Failed predictions are counted explicitly, not quietly called accurate.
        # The evaluator refuses to certify a report containing any such failure.
        keep = source & finite
        target = torch.where(source, target, 0)
        predicted = torch.where(finite, predicted, 0)
        frame_matches = batch.frames[:, None] == self.frames
        # Validate before publishing, without synchronizing the CPU every batch.
        self.unknown_frames += (~frame_matches.any(-1)).sum()
        frame = frame_matches.long().argmax(-1)
        groups = F.one_hot(frame, 6).T.double()
        code, code_valid = coordinates(values[..., :18], source)
        self.coordinate_errors[..., 0] += groups @ code_valid.double().sum(1)
        self.coordinate_errors[..., 1] += groups @ torch.where(
            code_valid, (out["code"] - code).double().abs(), 0
        ).sum(1)
        if "embedding" in out:
            # Missing bars have no market reconstruction target; do not let their
            # repeated placeholder embeddings dominate the collapse diagnostic.
            embedding_mask = source.any(-1)
            embedding = torch.where(embedding_mask[..., None], out["embedding"], 0).double().reshape(-1, model.config.embedding)
            moments = torch.stack((embedding.sum(0), embedding.square().sum(0)))
            if self.embedding_moments is None:
                self.embedding_moments = torch.zeros_like(moments)
            self.embedding_moments += moments
            self.embedding_count += embedding_mask.sum()
        error = predicted - target
        nonzero = keep & (target != 0)
        denominator = torch.where(nonzero, target.abs(), 1)
        relative = 100 * error / denominator
        fields = (keep.double(), error.abs(), error, relative.abs(), relative, nonzero.double())
        for index, field in enumerate(fields):
            mask = nonzero if index in (3, 4, 5) else keep
            self.raw[..., index] += groups @ torch.where(mask, field, 0).sum(1)
        moment_fields = (
            torch.ones_like(target),
            target,
            target.square(),
            predicted,
            predicted.square(),
            (out["logs"].double() - values[..., :18]).abs(),
        )
        for index, field in enumerate(moment_fields):
            self.moments[..., index] += groups @ torch.where(keep, field, 0).sum(1)
        self.denominators[..., 0] += groups @ (source & (target == 0)).double().sum(1)
        self.denominators[..., 1] += groups @ (
            source & (target.abs() > 0) & (target.abs() < 1e-8)
        ).double().sum(1)
        self.violations[1] += ((predicted < 0) & keep).sum()

        # Percent-error quantiles include every nonzero valid target, including
        # tiny denominators. Extreme tails remain visible in the overflow bucket.
        channel = torch.arange(18, device=target.device)[None, None, :]
        group_id = (frame[:, None, None] * 18 + channel).expand_as(target)
        bucket = torch.bucketize(relative.abs().contiguous(), self.edges)
        self.histogram.scatter_add_(
            0, (group_id * (len(self.edges) + 1) + bucket).flatten(), nonzero.double().flatten()
        )
        prices = (channel % 6 < 4).expand_as(target)
        band = torch.bucketize(target.contiguous(), self.band_edges, right=True)
        band_id = frame[:, None, None] * 6 + band
        band_mask = nonzero & prices
        band_values = torch.stack((torch.ones_like(target), error.abs(), relative.abs()), -1)
        self.bands.scatter_add_(
            0,
            band_id.flatten()[:, None].expand(-1, 3),
            torch.where(band_mask[..., None], band_values, 0).reshape(-1, 3),
        )

        y = target.reshape(-1, 64, 3, 6)
        p = predicted.reshape_as(y)
        m = keep.reshape_as(y)
        opened, high, low, close = y[..., :4].unbind(-1)
        po, ph, pl, pc = p[..., :4].unbind(-1)
        complete = m[..., :4].all(-1)
        self.violations[2] += (
            complete & ((ph < torch.maximum(po, pc)) | (pl > torch.minimum(po, pc)))
        ).sum()
        quote_valid = keep[..., 6:10] & keep[..., 12:16]
        self.violations[3] += (quote_valid & (predicted[..., 6:10] > predicted[..., 12:16])).sum()
        self.violations[4] += complete.sum()
        self.violations[5] += (m[..., 0] & m[..., 3] & (po <= 0)).sum()
        self.violations[6] += (m[..., 1] & m[..., 2] & (pl <= 0)).sum()
        self.violations[7] += (m[:, 1:, :, 3] & m[:, :-1, :, 3] & (pc[:, :-1] <= 0)).sum()

        def percent_change(a, b):
            return 100 * (a / torch.where(b > 0, b, 1) - 1)

        # Reuse established signed bins: unchanged, +/-0.05/0.1/0.25/0.5%.
        self.moves.moves(
            "candle",
            percent_change(close, opened),
            percent_change(pc, po),
            m[..., 0] & m[..., 3] & (po > 0),
            frame,
        )
        self.moves.moves(
            "range",
            percent_change(high, low),
            percent_change(ph, pl),
            m[..., 1] & m[..., 2] & (pl > 0),
            frame,
        )
        self.moves.moves(
            "consecutive_close",
            percent_change(close[:, 1:], close[:, :-1]),
            percent_change(pc[:, 1:], pc[:, :-1]),
            m[:, 1:, :, 3] & m[:, :-1, :, 3] & (pc[:, :-1] > 0),
            frame,
        )
        activity_target = target.index_select(-1, self.activity_indices)
        activity_prediction = predicted.index_select(-1, self.activity_indices)
        activity_keep = keep.index_select(-1, self.activity_indices)
        actual_delta = activity_target[:, 1:] - activity_target[:, :-1]
        predicted_delta = activity_prediction[:, 1:] - activity_prediction[:, :-1]
        pair_mask = activity_keep[:, 1:] & activity_keep[:, :-1]
        # Classes: down=0, unchanged=1, up=2. Balanced accuracy prevents a large
        # unchanged population from hiding a systematically wrong moving class.
        actual_class = actual_delta.sign().long() + 1
        predicted_class = predicted_delta.sign().long() + 1
        activity_group = (
            frame[:, None, None] * 6 + torch.arange(6, device=target.device)[None, None, :]
        )
        indices = activity_group * 9 + actual_class * 3 + predicted_class
        self.activity_confusion.scatter_add_(0, indices.flatten(), pair_mask.double().flatten())
        loss, _ = objective(model, out, values, valid)
        self.loss += loss.double() * len(values)
        self.windows += len(values)

    def result(self):
        """Transfer bounded aggregates once; derive human/W&B-friendly scalars."""
        if not self.windows:
            raise ValueError("Cannot report empty evaluation")
        if int(self.unknown_frames):
            raise ValueError("Unknown timeframe in metrics")
        base = dict(
            windows=self.windows,
            bars=self.windows * 64,
            loss=float(self.loss / self.windows),
            nonfinite=int(self.violations[0]),
            raw=self.raw.cpu().tolist(),
            moves=self.moves.finish(),
        )
        measured = price_scalars(base)
        moments = self.moments.cpu().numpy()
        confusion = self.activity_confusion.reshape(6, 6, 3, 3).cpu().numpy()
        histogram = self.histogram.reshape(6, 18, -1).cpu().numpy()
        edges = self.edges.cpu().numpy()
        bands = self.bands.reshape(6, 6, 3).cpu().numpy()
        denominators = self.denominators.cpu().numpy()
        coordinate_errors = self.coordinate_errors.cpu().numpy()
        for f, frame in enumerate(FRAME_NAMES):
            for j, name in enumerate(CHANNELS):
                prefix = f"quality/{frame}/{name}"
                n, ts, tss, ps, pss, log_error = moments[f, j]
                if n:
                    measured[prefix + "/target_variance"] = max(0.0, tss / n - (ts / n) ** 2)
                    measured[prefix + "/prediction_variance"] = max(0.0, pss / n - (ps / n) ** 2)
                    measured[prefix + "/log_mae"] = log_error / n
                measured[prefix + "/zero_target_count"] = denominators[f, j, 0]
                measured[prefix + "/tiny_nonzero_target_count"] = denominators[f, j, 1]
                counts = histogram[f, j]
                for q, label in ((0.5, "median"), (0.9, "p90")):
                    if counts.sum():
                        index = np.searchsorted(counts.cumsum(), q * counts.sum())
                        if index < len(edges):
                            measured[prefix + f"/ape_{label}_upper_bound_pct"] = edges[index]
                measured[prefix + "/ape_above_1e6_pct_count"] = counts[-1]
                count, error = coordinate_errors[f, j]
                if count:
                    # Slot labels intentionally say coordinate: trade_volume is
                    # the shared quantity reference; trade_count is the ratio.
                    measured[f"coordinate_mae/{frame}/slot_{j}"] = error / count
            for j, name in enumerate(np.asarray(CHANNELS)[list(ACTIVITY)]):
                matrix = confusion[f, j]
                counts = matrix.sum(1)
                if counts.sum():
                    prefix = f"activity_direction/{frame}/{name}"
                    measured[prefix + "/count"] = counts.sum()
                    measured[prefix + "/accuracy_pct"] = 100 * matrix.trace() / counts.sum()
                    recalls = matrix.diagonal() / np.maximum(counts, 1)
                    measured[prefix + "/balanced_accuracy_pct"] = 100 * recalls[counts > 0].mean()
                    for k, label in enumerate(("down", "flat", "up")):
                        measured[prefix + f"/{label}_count"] = counts[k]
                        if counts[k]:
                            measured[prefix + f"/{label}_recall_pct"] = 100 * recalls[k]
            for band, label in enumerate(
                ("below_0.1", "0.1_1", "1_5", "5_20", "20_100", "100_plus")
            ):
                n, absolute, relative = bands[f, band]
                measured[f"price_band/{frame}/{label}/count"] = n
                if n:
                    measured[f"price_band/{frame}/{label}/mae"] = absolute / n
                    measured[f"price_band/{frame}/{label}/mape_pct"] = relative / n
        for name, count in zip(
            (
                "nonfinite",
                "negative",
                "ohlc_violations",
                "crossed_quote_fields",
                "complete_ohlc_count",
                "candle_nonpositive_prediction_denominator",
                "range_nonpositive_prediction_denominator",
                "close_change_nonpositive_prediction_denominator",
            ),
            self.violations.cpu().tolist(),
        ):
            measured["output/" + name] = count
        embedding_count = int(self.embedding_count)
        if embedding_count:
            sums, squares = self.embedding_moments.cpu().numpy()
            variance = np.maximum(
                0, squares / embedding_count - (sums / embedding_count) ** 2
            )
            measured["embedding/variance_mean"] = float(variance.mean())
            measured["embedding/near_constant_dimensions"] = int((variance < 1e-8).sum())
        return dict(
            **base,
            metrics=measured,
            activity_confusion=confusion.tolist(),
            quantile_policy="Histogram upper bounds; overflow above 1e6 percent counted separately",
            loss_policy="Window-weighted mean of batch objectives; compare with fixed batch configuration",
        )
