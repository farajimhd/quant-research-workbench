"""Factory-issued source/profile preparation before native V4 writers.

The session carries one operation's exact inherited entry authorities. It
does not grant an exit, publish a strategy or replace a cold recovery proof.
"""
from contextlib import closing
from dataclasses import dataclass
from weakref import WeakKeyDictionary

_SESSIONS=WeakKeyDictionary()
_RUNTIMES=WeakKeyDictionary()


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class PreparedStructuralRejectionSession:
    source: object
    entry_authorities: tuple
    profile: object

    def require(self,*,market,candidates,entry,through_boundary_ms,run_id,number):
        from .backtest_profit_armed_structural_rejection_management import require_prepared_structural_rejection_source
        from src.trading_runtime.profit_armed_structural_rejection_profile import require_native_structural_rejection_profile
        if type(self) is not PreparedStructuralRejectionSession or self not in _SESSIONS:
            raise ValueError('Unissued prepared structural rejection session')
        original=_SESSIONS[self]
        if (original[:3]!=(id(market),id(candidates),id(entry))
                or original[3:6]!=(through_boundary_ms,run_id,number)
                or self.source is not original[6] or self.entry_authorities is not original[7]
                or self.profile is not original[8]):
            raise ValueError('Structural rejection session changed certified operation')
        require_prepared_structural_rejection_source(self.source)
        require_native_structural_rejection_profile(self.profile)
        if (self.profile.source is not self.source or self.entry_authorities[-1] is not self.source.price_authority
                or self.source.lookup.through_boundary_ms!=through_boundary_ms):
            raise ValueError('Structural rejection session changed source/profile horizon')
        return self

    def bind_runtime(self,runtime):
        from src.trading_runtime.runtime import TradingRuntime,RunMode
        if self not in _SESSIONS:
            raise ValueError('Unissued prepared structural rejection session')
        stored=_SESSIONS[self]
        self.require(market=stored[9],candidates=stored[10],entry=stored[11],
            through_boundary_ms=stored[3],run_id=stored[4],number=stored[5])
        source=self.source
        if (type(runtime) is not TradingRuntime or runtime.config.mode is not RunMode.BACKTEST
                or runtime.run_id!=source.run_id or runtime.config.strategy_id!=source.strategy_id
                or runtime.config.strategy_revision!=source.strategy_revision
                or runtime.config.anchor_date!=source.session_date
                or getattr(runtime,'_structural_rejection_session',None) is not None
                or self in _RUNTIMES or getattr(runtime,'_fixed_structural_lot_session',None) is not None):
            raise ValueError('Structural rejection session requires its exact unbound Backtest runtime')
        _RUNTIMES[self]=runtime
        runtime._structural_rejection_session=self


def prepare_structural_rejection_session(*,plans,number,run_id,session_date,
        through_boundary_ms,client_factory):
    from .backtest_strategy_one_plan import StrategyOneFixedPlans
    from .backtest_strategy_one_execution import prepare_strategy_one_entry_authorities
    from .backtest_profit_armed_structural_rejection_management import prepare_structural_rejection_source
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    from src.trading_runtime.profit_armed_structural_rejection_profile import issue_prepared_structural_rejection_profile
    from src.trading_runtime.profit_armed_structural_rejection_native_policy import native_structural_rejection_declaration
    if type(plans) is not StrategyOneFixedPlans:
        raise ValueError('Structural rejection preparation requires whole certified native plans')
    contract=numbered_fixed_strategy(number)
    if native_structural_rejection_declaration(contract) is None:
        raise ValueError('Undeclared structural rejection session preparation')
    authorities=prepare_strategy_one_entry_authorities(market=plans.market,candidates=plans.candidates,
        entry=plans.entry,through_boundary_ms=through_boundary_ms,strategy_number=number,
        run_id=run_id,client_factory=client_factory)
    if type(authorities) is not tuple or len(authorities)!=7 or authorities[-1] is None:
        raise ValueError('Structural rejection session lacks inherited certified price authority')
    with closing(client_factory()) as client:
        source=prepare_structural_rejection_source(client,contract=contract,strategy_id=contract.strategy_id,
            strategy_revision=number,run_id=run_id,session_date=session_date,market=plans.market,
            seeds=plans.seeds,intervals=plans.v7_intervals,price_authority=authorities[-1],
            through_boundary_ms=through_boundary_ms)
    profile=issue_prepared_structural_rejection_profile(source)
    prepared=PreparedStructuralRejectionSession(source,authorities,profile)
    _SESSIONS[prepared]=(id(plans.market),id(plans.candidates),id(plans.entry),through_boundary_ms,
        run_id,number,source,authorities,profile,plans.market,plans.candidates,plans.entry)
    return prepared


def bind_structural_rejection_session_manager(prepared,manager):
    from .backtest_profit_armed_structural_rejection_management import bind_prepared_structural_rejection_manager
    from src.trading_runtime.profit_armed_structural_rejection_profile import bind_prepared_structural_rejection_profile
    if (type(prepared) is not PreparedStructuralRejectionSession or prepared not in _RUNTIMES
            or manager.runtime is not _RUNTIMES[prepared]
            or getattr(manager.runtime,'_structural_rejection_session',None) is not prepared):
        raise ValueError('Structural rejection manager lacks its actual bound prepared runtime')
    stored=_SESSIONS[prepared]
    prepared.require(market=stored[9],candidates=stored[10],entry=stored[11],
        through_boundary_ms=stored[3],run_id=stored[4],number=stored[5])
    owner=bind_prepared_structural_rejection_manager(manager,prepared.source)
    manager.bind_structural_rejection_management(owner)
    bind_prepared_structural_rejection_profile(prepared.profile,owner)
    return owner
