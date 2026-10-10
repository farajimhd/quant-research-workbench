"""V3 [B,P] representation: class IDs, bounded policy values, atomic clauses.

Broker/account/source authority and objective weights are contracts, not genes.
History buffers are padded to fixed caps. All values update captured
GPU buffers in place without compiling a different runner for every threshold.
"""

import json
from dataclasses import asdict, dataclass, fields, replace
from hashlib import sha256

import numpy as np

from .grid import Candidate, Settings
from .rules import ATOMS, CLAUSES, HISTORY, Compare, Temporal, validate_clause
from .timing import TIMING_CONTRACT, timing_fingerprint
from .history_bank import SWING_WINDOWS

VERSION = "semantic-squeeze-search-v3-5-risk-time"
MUTATION_CONTRACT = dict(
    version="seeded-mixed-strength-v1",
    probabilities=[.60, .30, .10],
    coordinate_rates=[.04, .12, .30],
    scales=[.01, .04, .15],
    wide_positive_steps="log1p when lower>=0 and upper>=1000",
)
ENTRY = ("signal", "hold", "retest", "macd")
ALLOCATION = ("equal", "decreasing", "increasing")
POLICY_FIELDS = (
    # name, lower, upper, integer. Durations are elapsed seconds, not UTC time.
    ("maximum_spread_fraction", 0.0001, 0.05, False),
    ("minimum_dollar_volume", 0, 1_000_000, False),
    ("minimum_trade_count", 0, 1000, True),
    ("initial_stop_fraction", 0.002, 0.20, False),
    ("target_step_fraction", 0.005, 0.20, False),
    ("adaptive_window", 2, 32, True),
    ("adaptive_multiplier", 0.5, 10, False),
    ("minimum_trail_fraction", 0.001, 0.10, False),
    ("trail_up_fraction", 0.005, 0.20, False),
    ("trail_stop_fraction", 0.001, 0.10, False),
    ("entry_deadline_seconds", 1, 30, True),
    ("remainder_policy_id", 0, 3, True),
    ("retry_interval_seconds", 1, 30, True),
    ("maximum_retries", 0, 30, True),
    ("maximum_total_order_age_seconds", 1, 300, True),
    ("maximum_chase_bps", 0, 500, False),
    ("require_signal_valid", 0, 1, True),
    ("maximum_signal_age_seconds", 1, 57_600, True),
    ("maximum_entry_drift_fraction", 0.0001, 0.05, False),
    ("retest_tolerance_fraction", 0.0001, 0.05, False),
    ("retest_timeout_seconds", 1, 120, True),
    ("swing_left_seconds", 1, 60, True),
    ("swing_right_seconds", 1, 60, True),
    ("replacement_margin", 0, 1, False),
    ("replacement_confirm_seconds", 1, 30, True),
    ("replacement_cooldown_seconds", 0, 300, True),
    ("terminal_exit_lead_seconds", 1, 60, True),
    ("retest_lookback_seconds", 1, 60, True),
    ("momentum_lookback_seconds", 1, 60, True),
    ("attention_lookback_seconds", 1, 60, True),
    ("momentum_scale", 0.001, 0.20, False),
    ("strength_scale", 0.001, 0.20, False),
    ("attention_cap", 1, 10, False),
    ("momentum_weight", 0, 1, False),
    ("strength_weight", 0, 1, False),
    ("attention_weight", 0, 1, False),
    ("liquidity_weight", 0, 1, False),
    ("reward_risk_weight", 0, 1, False),
    ("stagnation_weight", 0, 1, False),
    ("stagnation_seconds", 1, 300, True),
)
NAMES = tuple(row[0] for row in POLICY_FIELDS)
HISTORY_FIELDS = (
    "adaptive_window",
    "swing_left_seconds",
    "swing_right_seconds",
    "retest_lookback_seconds",
    "momentum_lookback_seconds",
    "attention_lookback_seconds",
)
CLASSES = (
    (0, 1, 2, 3),
    tuple(range(16)),
    (0, 1),
    tuple(range(61)),
    tuple(range(1, 16)),
    (0, 1, 2),
    (0, 1),
    (0, 1),
    (0, 1),
    (0, 1),
)


@dataclass(frozen=True)
class Decoded:
    candidate: Candidate
    settings: Settings
    clauses: tuple
    connectors: tuple


class StrategySpace:
    def __init__(self, settings=Settings()):
        self.settings = settings.validate()
        self.repair_counts = dict(calls=0, rows=0, changed_coordinates=0)
        self.policy_start = 10
        self.rules_start = self.policy_start + len(POLICY_FIELDS)
        self.connectors_start = self.rules_start + CLAUSES * 6
        self.size = self.connectors_start + CLAUSES - 1
        self.default = np.array(
            [0, 0, 0, 0, 10, 0, 0, 0, 0, 0]
            + [getattr(settings, name) for name in NAMES]
            + [0, int(Compare.GREATER), 0, int(Temporal.CURRENT), 1, 1] * CLAUSES
            + [0] * (CLAUSES - 1),
            dtype=np.float64,
        )

    def manifest(self):
        searched = set(NAMES)
        return dict(
            version=VERSION,
            timing_contract=dict(TIMING_CONTRACT),
            timing_fingerprint=timing_fingerprint(),
            shape=["B", self.size],
            policy_fields=[
                dict(
                    name=n,
                    lower=lo,
                    upper=hi,
                    integer=integer,
                    role="policy_class_id" if n in ('remainder_policy_id', 'require_signal_valid') else "bounded_policy_value",
                )
                for n, lo, hi, integer in POLICY_FIELDS
            ],
            candidate_classes=dict(
                remainder={0: 'cancel_partial', 1: 'retain', 2: 'reprice', 3: 'resubmit'},
                entry=dict(enumerate(ENTRY)),
                allocation=dict(enumerate(ALLOCATION)),
                positions=list(range(1, 16)),
                target={0: "percentage", 1: "structural"},
                trailing={0: "step", 1: "adaptive"},
                stop={0: "percentage", 1: "swing"},
                replacement={0: False, 1: True},
                macd_mask=list(range(16)),
            ),
            atomic_inputs=[asdict(atom) for atom in ATOMS],
            comparison_classes={int(v): v.name for v in Compare},
            temporal_classes={int(v): v.name for v in Temporal},
            clause_layout=[
                "enabled",
                "comparison_id",
                "input_id",
                "temporal_id",
                "lookback",
                "threshold",
            ],
            threshold_role="numeric literal of selected input semantic kind; no register references",
            random_value_density="log1p for nonnegative wide price/activity thresholds; uniform for other values; all class IDs random",
            connector_classes={0: "AND", 1: "OR"},
            history_capacity=HISTORY,
            history_allocation=dict(
                atomic=12, adaptive=32, swing=121, retest=60, momentum=60, attention=60
            ),
            candidate_layout=[
                "entry_id",
                "macd_mask",
                "macd_all_id",
                "hold_seconds",
                "positions",
                "allocation_id",
                "target_id",
                "trailing_id",
                "stop_id",
                "replacement_id",
            ],
            normalization="Inactive hold/MACD coordinates become zero; activating an empty hold/MACD uses one; numeric bounds and trail-stop<=trail-up coupling are repaired and counted",
            hold_seconds=list(range(1, 61)),
            macd_all_classes={0: "ANY", 1: "ALL"},
            fixed_settings={
                f.name: dict(
                    value=getattr(self.settings, f.name),
                    reason="Source/broker/account/objective contract",
                )
                for f in fields(Settings)
                if f.name not in searched
            },
            masked_history_fields=HISTORY_FIELDS,
            swing_window_ladder=list(SWING_WINDOWS),
        )

    def validate(self, values):
        rows = np.array(values, dtype=np.float64, copy=True)
        if rows.ndim != 2 or rows.shape[1] != self.size or not np.isfinite(rows).all():
            raise ValueError(f"Require finite [B,{self.size}] strategy tensor")
        for row in rows:
            for j, allowed in enumerate(CLASSES):
                if row[j] not in allowed and not (j == 3 and row[j] == 0):
                    raise ValueError("Unknown categorical/count ID")
            mode = int(row[0])
            if mode == 1:
                if row[3] < 1:
                    raise ValueError("Hold duration must be positive")
            elif row[3] != 0:
                raise ValueError("Inactive hold gene must be zero")
            if mode == 3:
                if not 1 <= row[1] <= 15:
                    raise ValueError("MACD subset cannot be empty")
                if int(row[1]).bit_count() == 1 and row[2]:
                    raise ValueError("Singleton MACD has one canonical mode")
            elif row[1] or row[2]:
                raise ValueError("Inactive MACD genes must be zero")
            for j, (_, lo, hi, integer) in enumerate(POLICY_FIELDS, self.policy_start):
                if not lo <= row[j] <= hi or (integer and row[j] != int(row[j])):
                    raise ValueError("Policy value outside declared range")
                if NAMES[j-self.policy_start] in ('swing_left_seconds','swing_right_seconds') and row[j] not in SWING_WINDOWS:
                    raise ValueError('Swing window must belong to the persisted ladder')
            if (
                row[self.policy_start + NAMES.index("trail_stop_fraction")]
                > row[self.policy_start + NAMES.index("trail_up_fraction")]
            ):
                raise ValueError("Stop step cannot exceed its required price rise")
            for clause in row[self.rules_start : self.connectors_start].reshape(
                CLAUSES, 6
            ):
                validate_clause(clause)
            if not np.isin(row[self.connectors_start :], (0, 1)).all():
                raise ValueError("Unknown Boolean connector")
        return rows

    def repair(self, values):
        """Canonicalize inactive genes and bounded numeric couplings, counted explicitly.

        Legal class IDs may be neutralized when their owning policy is inactive.
        Crossover activating hold/MACD repairs an empty dependent value to one.
        Unknown IDs always fail before any normalization.
        """
        rows = np.array(values, dtype=np.float64, copy=True)
        if rows.ndim != 2 or rows.shape[1] != self.size or not np.isfinite(rows).all():
            raise ValueError("Invalid strategy tensor")
        original = rows.copy()
        for row in rows:
            if any(
                value != int(value) or value not in choices
                for value, choices in zip(row[:10], CLASSES)
            ):
                raise ValueError("Unknown candidate class")
            if row[0] != 1:
                row[3] = 0
            elif row[3] == 0:
                row[3] = 1
            if row[0] != 3:
                row[1:3] = 0
            elif row[1] == 0:
                row[1] = 1
                row[2] = 0
            elif int(row[1]).bit_count() == 1:
                row[2] = 0
            for j, (_, lo, hi, integer) in enumerate(POLICY_FIELDS, self.policy_start):
                row[j] = np.clip(row[j], lo, hi)
                if integer:
                    row[j] = np.rint(row[j])
                if NAMES[j-self.policy_start] in ('swing_left_seconds','swing_right_seconds'):
                    row[j]=min(SWING_WINDOWS,key=lambda value:abs(value-row[j]))
            stop = self.policy_start + NAMES.index("trail_stop_fraction")
            up = self.policy_start + NAMES.index("trail_up_fraction")
            row[stop] = min(row[stop], row[up])
            for clause in row[self.rules_start : self.connectors_start].reshape(
                CLAUSES, 6
            ):
                label = clause[2]
                if label != int(label) or not 0 <= label < len(ATOMS):
                    raise ValueError("Unknown atomic input")
                if (
                    clause[0] not in (0, 1)
                    or clause[1] not in tuple(Compare)
                    or clause[3] not in tuple(Temporal)
                ):
                    raise ValueError("Unknown atomic operation class")
                atom = ATOMS[int(label)]
                if not atom.history and clause[3] != Temporal.CURRENT:
                    clause[3] = Temporal.CURRENT
                clause[4] = np.rint(np.clip(clause[4], 1, HISTORY))
                clause[5] = np.clip(clause[5], atom.lower, atom.upper)
        result = self.validate(rows)
        self.repair_counts["calls"] += 1
        self.repair_counts["rows"] += len(rows)
        self.repair_counts["changed_coordinates"] += int(
            np.count_nonzero(original != rows)
        )
        return result

    def decode(self, values):
        result = []
        for row in self.validate(values):
            candidate = Candidate(
                ENTRY[int(row[0])],
                int(row[1]),
                bool(row[2]),
                int(row[3]),
                int(row[4]),
                ALLOCATION[int(row[5])],
                ("percentage", "structural")[int(row[6])],
                ("step", "adaptive")[int(row[7])],
                ("percentage", "swing")[int(row[8])],
                bool(row[9]),
            ).validate()
            policy = {
                name: int(row[j]) if integer else float(row[j])
                for j, (name, _, _, integer) in enumerate(
                    POLICY_FIELDS, self.policy_start
                )
            }
            settings = replace(self.settings, **policy).validate()
            result.append(
                Decoded(
                    candidate,
                    settings,
                    tuple(
                        tuple(v)
                        for v in row[self.rules_start : self.connectors_start].reshape(
                            CLAUSES, 6
                        )
                    ),
                    tuple(row[self.connectors_start :]),
                )
            )
        return result

    def group_key(self, row):
        self.validate([row])
        return ("padded-history-v3",)

    def sample(self, rng, count):
        rows = np.tile(self.default, (count, 1))
        for j, choices in enumerate(CLASSES):
            rows[:, j] = rng.choice(choices, count)
        for j, (name, lo, hi, _) in enumerate(POLICY_FIELDS, self.policy_start):
            if name in ('swing_left_seconds','swing_right_seconds'):
                rows[:,j]=rng.choice(SWING_WINDOWS,count)
            elif name in ("minimum_dollar_volume", "minimum_trade_count"):
                # A fixed random density across the SAME bounds. Uniform
                # million-dollar thresholds almost always exclude premarket.
                rows[:, j] = np.expm1(rng.uniform(np.log1p(lo), np.log1p(hi), count))
            else:
                rows[:, j] = rng.uniform(lo, hi, count)
        for row in rows:
            for clause in row[self.rules_start : self.connectors_start].reshape(
                CLAUSES, 6
            ):
                clause[:5] = [
                    rng.integers(2),
                    rng.integers(4),
                    rng.integers(len(ATOMS)),
                    rng.integers(5),
                    rng.integers(1, HISTORY + 1),
                ]
                atom = ATOMS[int(clause[2])]
                if atom.lower >= 0 and atom.upper >= 1000:
                    clause[5] = np.expm1(
                        rng.uniform(np.log1p(atom.lower), np.log1p(atom.upper))
                    )
                else:
                    clause[5] = rng.uniform(atom.lower, atom.upper)
        rows[:, self.connectors_start :] = rng.integers(2, size=(count, CLAUSES - 1))
        return self.repair(rows)

    def offspring(self, rng, a, b):
        """Seeded light/medium/broad mutations; every coordinate is eligible.

        Light children refine one parent. Broader children cross whole semantic
        clauses, then mutate class IDs, integer counts, numeric values and rule
        thresholds. The caller's checkpointed RNG owns all randomness.
        """
        mode = int(rng.choice(3, p=MUTATION_CONTRACT["probabilities"]))
        probability = MUTATION_CONTRACT["coordinate_rates"][mode]
        scale = MUTATION_CONTRACT["scales"][mode]
        child = (a if rng.random() < .5 else b).copy()
        if mode:
            child = np.where(rng.random(self.size) < .5, a, b)
            for i in range(CLAUSES):
                start = self.rules_start + i * 6
                parent = a if rng.random() < .5 else b
                child[start:start + 6] = parent[start:start + 6]
        proposal = self.sample(rng, 1)[0]
        # Class mutations replace IDs, never add fractional offsets to labels.
        for index in list(range(self.policy_start)) + list(range(self.connectors_start, self.size)):
            if rng.random() < probability:
                child[index] = proposal[index]
        for i, (name, lower, upper, integer) in enumerate(POLICY_FIELDS):
            index = self.policy_start + i
            if rng.random() < probability:
                if name in ('remainder_policy_id', 'require_signal_valid'):
                    child[index] = proposal[index]
                else:
                    # Wide positive ranges need relative steps: a $100 filter
                    # must not jump by $10,000 in a nominally light mutation.
                    if lower >= 0 and upper >= 1000:
                        value = np.expm1(
                            np.log1p(child[index])
                            + rng.normal(0, scale * (np.log1p(upper) - np.log1p(lower)))
                        )
                        delta = value - child[index]
                    else:
                        delta = rng.normal(0, scale * (upper - lower))
                    if integer:
                        delta = (1 if delta >= 0 else -1) * max(1, abs(round(delta)))
                    child[index] = np.clip(child[index] + delta, lower, upper)
        for i in range(CLAUSES):
            start = self.rules_start + i * 6
            old_label = child[start + 2]
            for offset in range(5):
                if rng.random() < probability:
                    child[start + offset] = proposal[start + offset]
            atom = ATOMS[int(child[start + 2])]
            # Changing input changes threshold units: reset the literal together.
            if child[start + 2] != old_label:
                child[start + 5] = rng.uniform(atom.lower, atom.upper)
            child[start + 5] = np.clip(child[start + 5], atom.lower, atom.upper)
            if rng.random() < probability:
                if atom.lower >= 0 and atom.upper >= 1000:
                    value = np.expm1(
                        np.log1p(child[start + 5])
                        + rng.normal(0, scale * (np.log1p(atom.upper) - np.log1p(atom.lower)))
                    )
                else:
                    value = child[start + 5] + rng.normal(0, scale * (atom.upper - atom.lower))
                child[start + 5] = np.clip(value, atom.lower, atom.upper)
        return self.repair([child])[0]

    def identity(self, row):
        self.validate([row])
        return sha256(json.dumps(list(map(float, row))).encode()).hexdigest()
