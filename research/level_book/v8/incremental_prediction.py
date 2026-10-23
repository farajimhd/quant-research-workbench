"""Encounter labels and predict-before-update count model for notebook 08.

Future prices are used only to construct evaluation labels. The learner admits
each label strictly after its resolution event, never at its prediction event.
"""
import heapq
import numpy as np
import polars as pl


def encounters(replay, distance=200):
    """Two cents = 200 price units. Preserve notebook-07 strict band breaks.

    Only a fresh entry from the role's approach side, AFTER role activation,
    starts an encounter. One pending encounter per role period. Gap-through
    trades are not touches. Final unresolved encounters retain null labels.
    """
    assert distance > 0
    prices = replay.frame['price_units'].to_numpy()
    times = replay.times
    rows = []
    for pid, period in enumerate(replay.periods):
        band, role, origin, start, parent = map(int, period)
        lo = int(replay.values[replay.lower[band]])
        hi = int(replay.values[replay.upper[band]])
        end = int(replay.ends[pid]) or len(prices)
        # Zero-based index start is the event immediately AFTER activation.
        ix = np.arange(start, end)
        inside = (prices[ix] >= lo) & (prices[ix] <= hi)
        approach = prices[ix-1] > hi if role == 0 else prices[ix-1] < lo
        touches = ix[inside & approach]
        favorable = ix[prices[ix] >= hi+distance] if role == 0 else ix[prices[ix] <= lo-distance]
        broken = int(replay.ends[pid])
        busy_until = -1
        for touch in touches:
            if touch <= busy_until:
                continue
            k = np.searchsorted(favorable, touch, side='right')
            reject = int(favorable[k])+1 if k < len(favorable) else 0
            resolved = reject if reject and (not broken or reject < broken) else broken
            outcome = int(resolved == reject) if resolved else None
            age = (times[touch]-times[start-1])/1e6
            rows.append(dict(period_id=pid, band_id=band, role=role,
                             touch_event=int(touch)+1, time_us=int(times[touch]),
                             age_seconds=float(age), age_group=int(age >= 60),
                             lower_units=lo, upper_units=hi,
                             resolve_event=resolved or None, outcome=outcome,
                             duration_seconds=float((times[resolved-1]-times[touch])/1e6) if resolved else None))
            busy_until = resolved-1 if resolved else len(prices)
    return pl.DataFrame(rows).sort('touch_event', 'period_id')


class CountModel:
    """Small checkpointable aggregates; each array is [R, B, seconds_R, seconds_B]."""
    def __init__(self, weight=4.):
        assert np.isfinite(weight) and weight > 0
        self.weight = weight
        self.groups = np.zeros((2, 2, 4))  # role, age-at-touch group, aggregate
        self.personal = {}  # role-period identity -> age-group aggregates
        self.total = np.zeros(4)

    def predict(self, pid, role, group):
        own = self.personal.get(pid, np.zeros((2, 4)))
        shared = self.groups[role, group] - own[group]
        p = (shared[0]+1)/(shared[0]+shared[1]+2)
        r, b = own[:, :2].sum(axis=0)
        score = (r+self.weight*p)/(r+b+self.weight)
        role_counts = self.groups[role].sum(axis=0)
        return dict(prediction=float(score), group_prediction=float(p),
                    role_prediction=float((role_counts[0]+1)/(role_counts[0]+role_counts[1]+2)),
                    personal_rejections=int(r), personal_breaks=int(b),
                    shared_rejections=int(shared[0]), shared_breaks=int(shared[1]),
                    mean_rejection_seconds=float(shared[2]/shared[0]) if shared[0] else None,
                    global_prediction=float((self.total[0]+1)/(self.total[0]+self.total[1]+2)))

    def update(self, row):
        bucket = 0 if row['outcome'] == 1 else 1
        value = np.zeros(4)
        value[bucket] = 1
        value[bucket+2] = row['duration_seconds']
        self.groups[row['role'], row['age_group']] += value
        self.personal.setdefault(row['period_id'], np.zeros((2, 4)))[row['age_group']] += value
        self.total += value


def predict_online(table, cutoff, final_event, initial_groups=None):
    """Same-event labels wait until the next event; tied touches share history."""
    model = CountModel()
    if initial_groups is not None:
        seed = np.asarray(initial_groups, dtype=float)
        if seed.shape != (2, 2, 4) or not np.isfinite(seed).all() or (seed < 0).any():
            raise ValueError('Expected nonnegative finite role/age aggregates with shape (2,2,4)')
        # Only shared evidence crosses sessions. Personal role identities restart.
        model.groups = seed.copy()
        model.total = seed.sum(axis=(0, 1))
    pending = []
    predictions = []
    updates = []
    for number, row in enumerate(table.iter_rows(named=True)):
        event = row['touch_event']
        while pending and pending[0][0] < event:
            _, _, completed = heapq.heappop(pending)
            model.update(completed)
            updates.append(completed['resolve_event'])
        pred = model.predict(row['period_id'], row['role'], row['age_group'])
        predictions.append(dict(**row, **pred, evaluation=event > cutoff,
                                latest_training_event=(updates[-1] if updates else 0)))
        if row['resolve_event'] is not None:
            heapq.heappush(pending, (row['resolve_event'], number, row))
    # At the snapshot, outcomes on its last event can now be incorporated.
    while pending and pending[0][0] <= final_event:
        _, _, row = heapq.heappop(pending)
        model.update(row)
    return pl.DataFrame(predictions), model


def metrics(rows, column):
    y = rows['outcome'].to_numpy().astype(float)
    p = np.clip(rows[column].to_numpy(), 1e-9, 1-1e-9)
    label = p >= .5
    positives = y == 1
    ranks = pl.Series(p).rank(method='average').to_numpy()
    n_positive = int(positives.sum())
    n_negative = len(y)-n_positive
    auc = (ranks[positives].sum()-n_positive*(n_positive+1)/2)/(n_positive*n_negative)
    return dict(model=column, encounters=len(y), rejection_rate=float(y.mean()),
                accuracy=float((label == positives).mean()),
                balanced_accuracy=float(((label[positives]).mean()+(~label[~positives]).mean())/2),
                roc_auc=float(auc),
                brier=float(np.mean((p-y)**2)),
                log_loss=float(-np.mean(y*np.log(p)+(1-y)*np.log(1-p))))
