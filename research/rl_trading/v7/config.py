"""Explicit premarket research contract; no V2 checkpoint compatibility."""
from dataclasses import dataclass, asdict

VERSION = 'rl-v7-premarket-attention-1'


@dataclass(frozen=True)
class Config:
    initial_cash: float = 10000.
    allocation: float = .15
    stop_ratio: float = .02
    target_ratio: float = .04
    max_hold_seconds: int = 1800
    displayed_participation: float = .1
    extra_slippage: float = .0002
    commission_model: str = 'ibkr_us_fixed_20260926'
    extra_venue_fee_per_share: float = 0.
    width: int = 64
    heads: int = 4
    seed: int = 1917
    learning_rate: float = .0001
    rollout: int = 2048
    batch: int = 256
    ppo_epochs: int = 3
    gamma: float = 1.
    gae_lambda: float = .99

    def __post_init__(self):
        if not (0 < self.allocation <= 1 and 0 < self.displayed_participation <= 1
                and 0 < self.stop_ratio < 1 and self.target_ratio > 0
                and 0 <= self.extra_slippage < .1 and self.initial_cash > 0
                and self.max_hold_seconds > 0 and self.width % self.heads == 0):
            raise ValueError('Invalid research configuration')

    def manifest(self):
        return dict(version=VERSION, **asdict(self), decisions='04:00:01-09:29:59 America/New_York',
                    execution='next-second displayed bid/ask; consumed size per quote timestamp',
                    positions='one cash-funded long; holdings remain until exit even outside scanner',
                    tail='fixed stop/target/max-hold; liquidation from 19:45; invalid if not flat at20:00',
                    reward='own net account equity delta, percentage of initial cash',
                    labels=False, identity_features=False)
