"""Exact semantic grid; no random mutation and no duplicate singleton ANY/ALL."""

from dataclasses import asdict, dataclass
from hashlib import sha256
from itertools import combinations, product
import json
import math

VERSION = "squeeze-grid-v3-1"
MACD_SECONDS = (1, 5, 10, 30)
MAX_POSITIONS = 15


@dataclass(frozen=True)
class Candidate:
    entry: str
    macd_mask: int = 0
    macd_all: bool = False
    hold_seconds: int = 0
    positions: int = 10
    allocation: str = "equal"
    target: str = "percentage"
    trailing: str = "step"
    initial_stop: str = "percentage"
    replacement: bool = False

    def validate(self):
        if any(
            type(getattr(self, name)) is not int
            for name in ("macd_mask", "hold_seconds", "positions")
        ):
            raise ValueError("Candidate masks, counts and durations must be integers")
        if type(self.macd_all) is not bool or type(self.replacement) is not bool:
            raise ValueError("Candidate permissions must be Boolean")
        if self.entry not in ("signal", "hold", "retest", "macd"):
            raise ValueError("Unknown entry mode")
        if not 1 <= self.positions <= 15:
            raise ValueError("Position count outside the approved envelope")
        if self.allocation not in ("equal", "decreasing", "increasing"):
            raise ValueError("Unknown allocation")
        if self.target not in ("percentage", "structural") or self.trailing not in (
            "step",
            "adaptive",
        ):
            raise ValueError("Unknown target/trailing mode")
        if self.initial_stop not in ("percentage", "swing"):
            raise ValueError("Unknown initial stop")
        if self.entry == "hold":
            if not 1 <= self.hold_seconds <= 60:
                raise ValueError("Hold duration must be 1..60s")
        elif self.hold_seconds:
            raise ValueError("Unused hold parameter")
        if self.entry == "macd":
            if not 1 <= self.macd_mask <= 15 or (
                self.macd_mask.bit_count() == 1 and self.macd_all
            ):
                raise ValueError("Invalid or duplicate MACD subset")
        elif self.macd_mask or self.macd_all:
            raise ValueError("Unused MACD parameter")
        return self

    @property
    def identity(self):
        self.validate()
        return sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Settings:
    """Fixed research assumptions included in approval and resume identities."""

    clock_seconds: int = 1
    initial_cash: float = 10000.0
    participation: float = 0.10
    fee_per_share: float = 0.005
    minimum_order_fee: float = 1.0
    maximum_spread_fraction: float = 0.01
    minimum_dollar_volume: float = 1000.0
    minimum_trade_count: int = 5
    initial_stop_fraction: float = 0.03
    target_step_fraction: float = 0.025
    price_tick: float = 0.01
    adaptive_window: int = 10
    adaptive_multiplier: float = 3.0
    minimum_trail_fraction: float = 0.01
    trail_up_fraction: float = 0.03
    trail_stop_fraction: float = 0.01
    entry_deadline_seconds: int = 5
    maximum_signal_age_seconds: int = 57600
    maximum_quote_age_seconds: float = 1.0
    maximum_entry_drift_fraction: float = 0.01
    retest_tolerance_fraction: float = 0.005
    retest_timeout_seconds: int = 30
    swing_left_seconds: int = 2
    swing_right_seconds: int = 2
    replacement_margin: float = 0.15
    replacement_confirm_seconds: int = 3
    replacement_cooldown_seconds: int = 30
    terminal_exit_lead_seconds: int = 10
    # Searchable ranking and history parameters, preserving v2 defaults.
    retest_lookback_seconds: int = 5
    momentum_lookback_seconds: int = 5
    attention_lookback_seconds: int = 10
    momentum_scale: float = 0.03
    strength_scale: float = 0.03
    attention_cap: float = 3.0
    momentum_weight: float = 0.30
    strength_weight: float = 0.20
    attention_weight: float = 0.20
    liquidity_weight: float = 0.15
    reward_risk_weight: float = 0.15
    stagnation_weight: float = 0.15
    stagnation_seconds: int = 60
    drawdown_weight: float = 0.5

    def validate(self):
        if any(
            not isinstance(v, (int, float))
            or isinstance(v, bool)
            or not math.isfinite(v)
            or v < 0
            for v in asdict(self).values()
        ):
            raise ValueError("Settings must be finite nonnegative numbers")
        integer_fields = (
            "clock_seconds",
            "minimum_trade_count",
            "adaptive_window",
            "entry_deadline_seconds",
            "maximum_signal_age_seconds",
            "retest_timeout_seconds",
            "swing_left_seconds",
            "swing_right_seconds",
            "replacement_confirm_seconds",
            "replacement_cooldown_seconds",
            "terminal_exit_lead_seconds",
            "retest_lookback_seconds",
            "momentum_lookback_seconds",
            "attention_lookback_seconds",
            "stagnation_seconds",
        )
        if any(type(getattr(self, name)) is not int for name in integer_fields):
            raise ValueError("Counts and durations must be integers")
        if self.clock_seconds != 1 or not 2 <= self.adaptive_window <= 64:
            raise ValueError("One-second clock and bounded adaptive history required")
        if not (
            0 < self.participation <= 1
            and self.initial_cash > 0
            and self.price_tick > 0
            and 0 < self.initial_stop_fraction < 1
            and self.target_step_fraction > 0
            and self.trail_up_fraction > 0
            and self.trail_stop_fraction > 0
            and self.minimum_trail_fraction > 0
            and self.adaptive_multiplier > 0
        ):
            raise ValueError("Invalid financial/trailing settings")
        if (
            min(
                self.entry_deadline_seconds,
                self.maximum_signal_age_seconds,
                self.retest_timeout_seconds,
                self.swing_left_seconds,
                self.swing_right_seconds,
                self.replacement_confirm_seconds,
                self.terminal_exit_lead_seconds,
            )
            < 1
        ):
            raise ValueError("Durations/history must be positive")
        if not all(
            1 <= getattr(self, name) <= 12
            for name in (
                "retest_lookback_seconds",
                "momentum_lookback_seconds",
                "attention_lookback_seconds",
            )
        ):
            raise ValueError("Completed history lookbacks require 1..12 seconds")
        if (
            min(
                self.momentum_scale,
                self.strength_scale,
                self.attention_cap,
                self.stagnation_seconds,
            )
            <= 0
        ):
            raise ValueError("Score scales and stagnation duration must be positive")
        return self


def build_grid():
    entries = [
        dict(entry="signal"),
        dict(entry="hold", hold_seconds=2),
        dict(entry="hold", hold_seconds=5),
        dict(entry="retest"),
    ]
    for length in range(1, 5):
        for subset in combinations(range(4), length):
            mask = sum(1 << i for i in subset)
            for all_required in (False,) if length == 1 else (False, True):
                entries.append(
                    dict(entry="macd", macd_mask=mask, macd_all=all_required)
                )
    result = [
        Candidate(
            **entry,
            positions=m,
            allocation=w,
            target=t,
            trailing=trail,
            initial_stop=stop,
            replacement=rotate,
        ).validate()
        for entry, m, w, t, trail, stop, rotate in product(
            entries,
            (5, 10, 15),
            ("equal", "decreasing", "increasing"),
            ("percentage", "structural"),
            ("adaptive", "step"),
            ("percentage", "swing"),
            (False, True),
        )
    ]
    assert len(result) == len({x.identity for x in result}) == 4320
    return result


def grid_manifest(settings=Settings()):
    settings.validate()
    from .runtime import code_hash

    value = {
        "version": VERSION,
        "settings": asdict(settings),
        "implementation_sha256": code_hash(),
        "candidate_count": 4320,
        "candidates": [asdict(c) for c in build_grid()],
        "execution_contract": "completed-1s-next-interval-quote-bound-v2",
        "capital": "cash-after-pending-buys-and-protective-fee-reserves-one-batch-ranked-tickers",
        "structural": "entry-frozen-distinct-resistance-lower-minus-tick",
        "rotation": "one-weakest-position-exit-then-revalidate-new-batch",
    }
    value["approval_digest"] = sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return value
