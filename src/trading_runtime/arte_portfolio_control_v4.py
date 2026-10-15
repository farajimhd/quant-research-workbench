"""V4 envelope for the existing normalized shared Portfolio control family."""
from dataclasses import dataclass
from types import MappingProxyType

from src.backend.backtest_portfolio_control_v3 import (
    CONTROL, project_portfolio_control_v3, seal_portfolio_control_v3,
)


@dataclass(frozen=True, slots=True)
class V4PortfolioControlBatch:
    base: object
    control: object

    def __post_init__(self):
        from .arte_journal_writer import TypedJournalBatch
        if type(self.base) is not TypedJournalBatch or self.base.status != 'running' or len(self.base.events) != 1:
            raise ValueError('Portfolio control requires its exact running V4 parent')
        seal_portfolio_control_v3((dict(self.control),), self.base.events,
            run_id=self.base.run_id, batch_id=self.base.batch_id)
        object.__setattr__(self, 'control', MappingProxyType(dict(self.control)))
