"""Versioned research assumptions. Ratios are fractions, not percentages or bps."""
from dataclasses import asdict, dataclass
import math

VERSION = 'rl-trading-v2-ppo-1'
# Upper bounds are inclusive, except the first band which excludes $1.
SHARE_CAPS = ((1., 40000), (5., 35000), (10., 30000), (20., 25000),
              (50., 20000), (None, 15000))


def share_cap(price: float) -> int:
    if not math.isfinite(price) or price <= 0:
        raise ValueError('Share caps require a positive finite price')
    if price < 1:
        return 40000
    return next(cap for upper, cap in SHARE_CAPS[1:] if upper is None or price <= upper)


@dataclass(frozen=True)
class Config:
    entry_rank: int = 100
    hold_rank: int = 120
    history_seconds: int = 60
    initial_cash: float = 10000.
    max_ticker_weight: float = 1.  # No extra percentage cap was chosen by the user.
    min_volume_60s: float = 20000.
    min_trades_60s: int = 11
    max_price_age_seconds: int = 5
    liquidation_buffer_seconds: int = 120
    # Uncalibrated price-only execution assumptions; replace with measured values.
    fee_ratio: float = .0001
    fee_per_share: float = 0.
    minimum_fee: float = 0.
    base_slippage_ratio: float = .0005  # Includes assumed half-spread; no extra spread fee.
    impact_ratio: float = .001
    volatility_slippage_ratio: float = .1
    max_volume_participation: float = .1

    def __post_init__(self):
        for name, value in asdict(self).items():
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f'Invalid configuration: {name}')
        for name in ('entry_rank', 'hold_rank', 'history_seconds', 'min_trades_60s',
                     'max_price_age_seconds', 'liquidation_buffer_seconds'):
            if type(getattr(self, name)) is not int:
                raise ValueError(f'{name} must be an integer')
        if (not 1 <= self.entry_rank <= self.hold_rank or self.history_seconds < 1
                or self.initial_cash <= 0 or not 0 < self.max_ticker_weight <= 1
                or not 0 < self.max_volume_participation <= 1
                or self.fee_ratio >= 1 or self.base_slippage_ratio >= 1):
            raise ValueError('Invalid account, universe, or execution configuration')

    def manifest(self):
        return dict(version=VERSION, **asdict(self), share_caps=SHARE_CAPS,
                    execution_assumptions='uncalibrated_price_only', latency_seconds=1,
                    order_type='next_second_IOC', reward='delta_equity_over_initial_equity')
