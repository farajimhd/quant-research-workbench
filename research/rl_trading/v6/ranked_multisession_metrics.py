"""Pool same-checkpoint session evidence without averaging class F1 scores."""
import math
import numpy as np


def _integer(value):
    if not math.isfinite(value) or value < 0 or abs(value-round(value)) > 1e-7:
        raise ValueError('Metric does not represent an exact nonnegative count')
    return int(round(value))


def _f1(tp, actual, predicted):
    return 2*tp/(actual+predicted) if actual+predicted else 0.


def _mae(parts):
    count = 0; total = 0.; weight = 0.
    for value, n, mass, error_sum in parts:
        n = _integer(n)
        if not math.isfinite(mass) or not math.isfinite(error_sum) or mass < 0 or error_sum < 0:
            raise ValueError('Finite nonnegative regression evidence required')
        if n:
            if value is None or not math.isfinite(value) or value < 0:
                raise ValueError('Observed regression targets require finite errors')
            if mass <= 0 or not math.isclose(value*mass, error_sum, rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError('Regression error sum and sample weights disagree with MAE')
        elif mass or error_sum or value is not None:
            raise ValueError('Empty regression targets require zero evidence')
        count += n; total += error_sum; weight += mass
    return (total/weight if weight else None), count, weight, total


def pool_gate_metrics(reports):
    """Caller must evaluate every session using the same frozen checkpoint.

    TrainingMetrics stores current true/predicted counts and recall, allowing
    exact true-positive recovery. Future reports contain full confusion counts.
    Missing classes within one session are allowed; global gate coverage remains
    mandatory. No metric is borrowed from a different checkpoint or epoch.
    """
    if not reports:
        raise ValueError('Nonempty same-checkpoint reports required')
    names = ('wait', 'enter_long', 'hold', 'exit_long')
    counts = {}; scores = {}
    for name in names:
        actual = predicted = tp = 0
        for r in reports:
            n = _integer(r['action_class_counts'][name])
            p = _integer(r['action_predicted_class_counts'][name])
            t = _integer(r['action_class_recall'][name]*n)
            if t > min(n, p):
                raise ValueError('Current class evidence is inconsistent')
            actual += n; predicted += p; tp += t
        counts[name] = actual; scores[name] = _f1(tp, actual, predicted)
    future = []
    for h in range(5):
        matrix = np.zeros((4, 4), dtype=np.int64)
        for r in reports:
            if len(r['forecast_label_metrics']) != 5:
                raise ValueError('Five future horizons required')
            source = np.asarray(r['forecast_label_metrics'][h]['confusion'])
            if source.shape != (4, 4) or not np.isfinite(source).all() or (source < 0).any() or (source != np.floor(source)).any():
                raise ValueError('Invalid future confusion counts')
            matrix += source.astype(np.int64)
        labels = {}
        for i, name in enumerate(('ENTRY', 'WAIT', 'HOLD', 'EXIT')):
            n = int(matrix[i].sum()); p = int(matrix[:, i].sum())
            labels[name] = dict(count=n, f1=_f1(int(matrix[i, i]), n, p))
        future.append(dict(labels=labels, confusion=matrix.tolist()))
    ratio, ratio_count, ratio_weight, ratio_sum = _mae((r['allocation_ratio_mae'], r['allocation_targets'], r['allocation_weight'], r['allocation_error_sum']) for r in reports)
    quality = {}; quality_counts = {}; quality_weights = {}; quality_sums = {}
    future_quality = []; future_quality_counts = []; future_weights = []; future_sums = []
    for name in ('ENTRY', 'EXIT'):
        quality[name], quality_counts[name], quality_weights[name], quality_sums[name] = _mae((r['action_quality_mae'][name], r['action_quality_counts'][name], r['action_quality_weights'][name], r['action_quality_error_sums'][name]) for r in reports)
    for h in range(5):
        values = {}; ns = {}; masses = {}; sums = {}
        for name in ('ENTRY', 'EXIT'):
            values[name], ns[name], masses[name], sums[name] = _mae((r['forecast_quality_mae'][h][name], r['forecast_quality_counts'][h][name], r['forecast_quality_weights'][h][name], r['forecast_quality_error_sums'][h][name]) for r in reports)
        future_quality.append(values); future_quality_counts.append(ns)
        future_weights.append(masses); future_sums.append(sums)
    return dict(action_class_counts=counts, action_class_f1=scores,
        forecast_label_metrics=future, allocation_ratio_mae=ratio,
        allocation_targets=ratio_count, allocation_weight=ratio_weight, allocation_error_sum=ratio_sum,
        action_quality_weights=quality_weights, action_quality_error_sums=quality_sums,
        forecast_quality_weights=future_weights, forecast_quality_error_sums=future_sums,
        action_quality_mae=quality,
        action_quality_counts=quality_counts, forecast_quality_mae=future_quality,
        forecast_quality_counts=future_quality_counts)
