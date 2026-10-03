"""Typed completed producer windows and the shared Strategy 45 gate."""
from dataclasses import dataclass
from math import isfinite
from uuid import UUID

MINIMUM_TRADES_60S = 59
MINIMUM_VOLUME_300S = 16145.075411885977


@dataclass(frozen=True, slots=True)
class LiquidityFact:
    ticker: str
    decision_ms: int
    completed_ms: int
    ready: bool
    trades_60s: int | None
    volume_300s: float | None
    fact_id: str
    source_token: str

    def validate(self):
        if (not self.ticker or type(self.decision_ms) is not int or self.decision_ms <= 0
                or self.decision_ms % 1000 or type(self.completed_ms) is not int
                or self.completed_ms != self.decision_ms // 30000 * 30000
                or type(self.ready) is not bool or not self.source_token
                or self.ready != (self.completed_ms >= 300000)
                or self.trades_60s is not None and (type(self.trades_60s) is not int or self.trades_60s < 0)
                or self.volume_300s is not None and (not isfinite(self.volume_300s) or self.volume_300s < 0)
                or self.ready and (self.trades_60s is None or self.volume_300s is None)
                or self.completed_ms and str(UUID(self.fact_id)) != self.fact_id
                or not self.completed_ms and self.fact_id):
            raise ValueError("Strategy 45 liquidity lacks its typed completed producer window")


def passes(fact):
    if type(fact) is not LiquidityFact:
        raise TypeError("Strategy 45 requires its certified liquidity fact")
    fact.validate()
    return fact.ready and fact.trades_60s >= MINIMUM_TRADES_60S and fact.volume_300s >= MINIMUM_VOLUME_300S


def breached(fact):
    fact.validate()
    return fact.ready and not passes(fact)
