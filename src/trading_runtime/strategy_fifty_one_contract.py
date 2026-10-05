"""Complete fixed swing ladder contract, independent from historical entry rules."""
from dataclasses import asdict, dataclass

from .squeeze_ladder_automatic import AutomaticLadderPolicy
from .squeeze_ladder_columnar import LadderGatePolicy
from .strategy_one_contract import STRATEGY_ID
from .signals import StrategyEvaluation


def ladder_gate_policy():
    return LadderGatePolicy(10000., 10000., 3., 3., 200., 10000,
        1_000_000, 300_000, 0, ((0, 19_500_000), (43_200_000, 57_000_000)),
        qualification_mode='vwap_cross')


def automatic_market_policy_payload():
    return dict(gate=asdict(ladder_gate_policy()), tick_int=100,
        stop_buffer_ticks=1, break_buffer_ticks=1,
        source_through_boundary_rule='extended_session_end',
        source_through_boundary_ms_by_session=dict(premarket=19_800_000, afterhours=57_600_000),
        population_exclusions=['LGHL'])


@dataclass(frozen=True, slots=True)
class Strategy51Contract:
    strategy_number: int = 51
    strategy_id: str = STRATEGY_ID
    execution_interval: str = '100ms'

    def __post_init__(self):
        if (type(self.strategy_number) is not int or self.strategy_number != 51
                or self.strategy_id != STRATEGY_ID or self.execution_interval != '100ms'):
            raise ValueError('Strategy51 fixed ladder identity differs')

    @property
    def automatic_entry_policy(self):
        return AutomaticLadderPolicy()

    @property
    def automatic_market_policy(self):
        return automatic_market_policy_payload()

    allows_session_exit = True
    allows_adds = False
    allows_reentry = False
    allows_trailing = False
    allows_replacement = False
    session_clocks = (19_500_000, 19_740_000, 19_800_000,
                      57_000_000, 57_300_000, 57_600_000)

    def acquisition_cutoff(self, boundary_ms):
        return (19_500_000 <= boundary_ms <= 19_800_000
            or 57_000_000 <= boundary_ms <= 57_600_000)

    def liquidation_due(self, boundary_ms):
        return (19_740_000 <= boundary_ms <= 19_800_000
            or 57_300_000 <= boundary_ms <= 57_600_000)


def strategy_fifty_one_contract():
    return Strategy51Contract()


class AssignedFixedSwingLadder51:
    strategy_id = STRATEGY_ID
    revision = 51
    automatic = True

    def __init__(self, assignments):
        from .strategy_engine import StrategyAssignment
        self.contract = strategy_fifty_one_contract()
        if (type(assignments) is not list or not assignments
                or any(type(row) is not StrategyAssignment
                    or (row.strategy_id, row.strategy_revision) != (STRATEGY_ID, 51)
                    or row.permissions != self.contract.automatic_entry_policy.permissions
                    for row in assignments)
                or len({(row.account_id, row.ticker) for row in assignments}) != len(assignments)):
            raise ValueError('Strategy51 requires unique immutable native assignments')
        self._assignments = tuple(assignments)

    def assignments(self):
        return self._assignments

    def bind_campaign_registry(self, registry):
        for assignment in self._assignments:
            registry.register(assignment)

    async def on_event(self, event, account_id) -> StrategyEvaluation:
        raise RuntimeError('Strategy51 requires certified completed boundaries')

    async def on_observation(self, observation, account_id) -> StrategyEvaluation:
        raise RuntimeError('Strategy51 cannot evaluate legacy strategy frames')
