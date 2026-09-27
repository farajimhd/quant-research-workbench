"""Versioned research assumptions. Ratios are fractions, not percentages or bps."""
from dataclasses import asdict, dataclass
import math

VERSION = 'rl-trading-v2-ppo-execution-open-2'
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
    entry_rank: int = 900
    hold_rank: int = 1000
    history_seconds: int = 60
    initial_cash: float = 10000.
    max_ticker_weight: float = 1.  # No extra percentage cap was chosen by the user.
    min_volume_60s: float = 20000.
    min_trades_60s: int = 11
    max_price_age_seconds: int = 5
    liquidation_buffer_seconds: int = 120
    commission_model: str = 'ibkr_us_fixed_20260926'
    extra_venue_fee_per_share: float = 0.
    # Broad parameterization bounds, not preferred durations or exit labels.
    minimum_stop_ratio: float = .001
    maximum_stop_ratio: float = .5
    minimum_target_ratio: float = .001
    maximum_target_ratio: float = 2.
    # Uncalibrated price-only execution assumptions; replace with measured values.
    fee_ratio: float = 0.
    fee_per_share: float = 0.
    minimum_fee: float = 0.
    base_slippage_ratio: float = .0005  # Includes assumed half-spread; no extra spread fee.
    impact_ratio: float = .001
    volatility_slippage_ratio: float = .1
    max_volume_participation: float = .1

    def __post_init__(self):
        for name, value in asdict(self).items():
            if name == 'commission_model':
                if value not in ('research','ibkr_us_fixed_20260926'):
                    raise ValueError('Unknown commission model')
                continue
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
        if not (0 < self.minimum_stop_ratio < self.maximum_stop_ratio < 1
                and 0 < self.minimum_target_ratio < self.maximum_target_ratio):
            raise ValueError('Invalid learned bracket distance bounds')
        if self.commission_model != 'research' and any((self.fee_ratio,self.fee_per_share,self.minimum_fee)):
            raise ValueError('Generic fee parameters require commission_model=research; IBKR fees are pinned')

    def manifest(self):
        from research.rl_trading.v2.fees import SCHEDULE
        from research.rl_trading.v2.estimated_luld import (
            PRIOR_CLOSE_MINIMUM, WINDOW_SECONDS, ESTIMATED_BAND_RATIO,
            BRACKET_BUFFER_RATIO)
        return dict(version=VERSION, **asdict(self), share_caps=SHARE_CAPS,
                    fee_schedule=SCHEDULE if self.commission_model != 'research' else None,
                    bracket_trigger='completed_second_close_then_next_second_IOC',
                    bracket_update='sample_on_entry; regular-hours effective levels clipped to estimated bands',
                    estimated_luld=dict(prior_close_minimum=PRIOR_CLOSE_MINIMUM,
                        window_seconds=WINDOW_SECONDS,band_ratio=ESTIMATED_BAND_RATIO,
                        bracket_buffer_ratio=BRACKET_BUFFER_RATIO,
                        provenance='research proxy from prior close and completed regular-session bars; not official SIP bands'),
                    duration_preference='none; gamma=1; session_only',
                    execution_assumptions='uncalibrated_next_second_open_price_only', latency_seconds=1,
                    order_type='next_second_open_IOC_proxy', reward='delta_equity_over_initial_equity')
