"""Operation-local installed session preparation before selected writers.

The read-only factory completes inherited admission/source certification first.
Neither a constructor nor a copied packet grants native or writer authority.
"""
from dataclasses import dataclass
from weakref import WeakKeyDictionary
from threading import RLock
from contextlib import closing

_SESSIONS = WeakKeyDictionary()
_LOCK = RLock()


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class PreparedFixedStructuralLotSession:
    operation: object
    entry_authorities: tuple
    profile: object

    def _require_issued(self):
        with _LOCK:
            binding = _SESSIONS.get(self)
        if binding is None:
            raise ValueError('Selected session lacks exact factory-issued source preparation')
        return self.require(market=binding[0], candidates=binding[1], entry=binding[2],
            through_boundary_ms=binding[3], run_id=binding[4], number=binding[5])

    def require(self, *, market, candidates, entry, through_boundary_ms, run_id, number):
        with _LOCK:
            binding = _SESSIONS.get(self)
        if (binding is None or binding[3:6] != (through_boundary_ms, run_id, number)
                or binding[6] is not self.operation or binding[7] is not self.entry_authorities
                or binding[8] is not self.profile):
            raise ValueError('Selected session lacks exact factory-issued source preparation')
        if (binding[0] is not market or binding[1] is not candidates or binding[2] is not entry):
            raise ValueError('Selected session uses another certified source operation')
        self.operation.source.require_installed_admission()
        from src.trading_runtime.fixed_structural_lot_profile import require_fixed_structural_lot_profile
        if require_fixed_structural_lot_profile(self.profile).operation is not self.operation:
            raise ValueError('Selected session profile differs from its installed operation')
        return self

    def bind_runtime(self, runtime):
        self._require_issued()
        if getattr(runtime, '_fixed_structural_lot_session', None) is not None:
            raise ValueError('Selected runtime session is already bound')
        self.operation.bind_runtime(runtime)
        runtime._fixed_structural_lot_session = self


def prepare_fixed_structural_lot_session(*, number, run_id, session_date,
        market, candidates, entry, seeds, through_boundary_ms, client_factory):
    from .backtest_strategy_one_execution import prepare_strategy_one_entry_authorities
    from .backtest_fixed_structural_lot_native import prepare_native_fixed_structural_lot_operation
    from src.trading_runtime.fixed_structural_lot_profile import issue_fixed_structural_lot_profile
    from .backtest_strategy_one_candidate_store import project_candidate_plan
    visible = project_candidate_plan(candidates, through_boundary_ms=through_boundary_ms)
    if not visible.prepared:
        from .backtest_fixed_structural_lot_empty import prepare_empty_fixed_structural_lot_source
        from .backtest_fixed_structural_lot_native import NativeFixedStructuralLotOperation
        with closing(client_factory()) as client:
            source = prepare_empty_fixed_structural_lot_source(client, number=number,
                run_id=run_id,session_date=session_date,market=market,candidates=candidates,
                through_boundary_ms=through_boundary_ms)
        operation = NativeFixedStructuralLotOperation(source)
        authorities = (visible,None,None,None,None,(),None)
    else:
        authorities = prepare_strategy_one_entry_authorities(market=market,
            candidates=candidates, entry=entry, through_boundary_ms=through_boundary_ms,
            strategy_number=number, run_id=run_id, client_factory=client_factory)
        if authorities[-1] is None:
            raise ValueError('Selected native session lacks inherited certified first-price authority')
        with closing(client_factory()) as client:
            operation = prepare_native_fixed_structural_lot_operation(client, number=number,
                run_id=run_id, session_date=session_date, market=market,
                seeds=seeds, price_authority=authorities[-1])
    profile = issue_fixed_structural_lot_profile(operation)
    prepared = PreparedFixedStructuralLotSession(operation, authorities, profile)
    with _LOCK:
        _SESSIONS[prepared] = (market, candidates, entry, through_boundary_ms,
            run_id, number, operation, authorities, profile)
    return prepared


def bind_fixed_structural_lot_manager(prepared, *, manager, publisher, session):
    """Bind the exact prepared live owner; cold restoration stays asynchronous."""
    from .backtest_fixed_structural_lot_management import NativeFixedStructuralLotManagement
    if type(prepared) is not PreparedFixedStructuralLotSession:
        raise ValueError('Selected manager lacks exact prepared session')
    prepared._require_issued()
    if (manager.runtime._fixed_structural_lot_session is not prepared
            or session != prepared.operation.source.session_date):
        raise ValueError('Selected manager differs from its prepared runtime operation')
    prepared.operation.source.require_installed_admission()
    bound_source = getattr(publisher, '_fixed_lot_source', None)
    if bound_source is None:
        prepared.operation.bind_publisher(publisher)
    elif bound_source is not prepared.operation.source:
        raise ValueError('Selected manager publisher contains another source operation')
    owner = NativeFixedStructuralLotManagement(operation=prepared.operation,
        publisher=publisher, client=publisher.writer._client)
    manager.bind_fixed_structural_lot_management(owner)
    return owner
