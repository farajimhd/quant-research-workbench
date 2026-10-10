"""Declared source-only retention; historical journal reads stay complete.

No caller may issue a proof or substitute an inventory here. Integration must
include these helpers in the operation's code guard and preserve cold fallback
when the loaded inventory exceeds its declared retention budget.
"""
from src.trading_runtime.first_inventory_source_reuse_policy import (
    PARAMETER, FirstInventorySourceReusePolicy,
    parse_declared_first_inventory_source_reuse,
)


def code_snapshot():
    """Bindings the issuing operation must capture and compare on every read."""
    from src.trading_runtime import first_inventory_source_reuse_policy as policy
    functions = (code_snapshot, selected_first_inventory_source_policy,
                 load_first_inventory, FirstInventorySourceReusePolicy.__init__,
                 FirstInventorySourceReusePolicy.__post_init__,
                 FirstInventorySourceReusePolicy.payload,
                 policy.require_declared_first_inventory_source_reuse,
                 policy.parse_declared_first_inventory_source_reuse)
    bindings = (FirstInventorySourceReusePolicy, policy.FirstInventorySourceReusePolicy,
                PARAMETER, policy.PARAMETER, policy.INPUT, policy.RULE,
                parse_declared_first_inventory_source_reuse,
                policy.parse_declared_first_inventory_source_reuse)
    return tuple((function, function.__code__) for function in functions), bindings


def selected_first_inventory_source_policy(source, operation_policy):
    payload = source.installed_payload
    if payload is None:
        return None
    from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
    from src.trading_runtime.initial_held_recovery_reuse_policy import InitialHeldRecoveryReusePolicy
    from src.trading_runtime.proposal_decision_inventory_reuse_policy import ProposalDecisionInventoryReusePolicy
    from src.trading_runtime.fixed_structural_lot_selected_exit_contract import FixedStructuralLotSelectedExitStrategyContract
    strategy = payload['strategy']
    release = numbered_strategy(strategy['strategy_number'])
    policy = parse_declared_first_inventory_source_reuse(
        release, strategy['parameters'].get(PARAMETER))
    if policy is None:
        return None
    source.require_prepared_source()
    source.require_installed_admission()
    if strategy['numbered_release']['contract'] != release.canonical_payload():
        raise ValueError('First-inventory installed declaration differs')
    contract = fixed_strategy_executor(release.executor_strategy_id,
                                      release.executor_revision).contract_factory()
    if (type(contract) is not FixedStructuralLotSelectedExitStrategyContract
            or contract.release != release
            or getattr(contract, PARAMETER, None) != policy):
        raise ValueError('First-inventory selected factory differs')
    contract.__post_init__()
    if type(operation_policy) is InitialHeldRecoveryReusePolicy:
        if contract.initial_held_recovery_reuse_policy != operation_policy:
            raise ValueError('First-inventory initial operation declaration differs')
        return policy if policy.initial_held else None
    if type(operation_policy) is ProposalDecisionInventoryReusePolicy:
        if contract.proposal_decision_inventory_reuse_policy != operation_policy:
            raise ValueError('First-inventory proposal operation declaration differs')
        return policy if policy.proposal else None
    raise ValueError('First-inventory source reuse needs a typed issued operation')


def load_first_inventory(operation, loader, *, inventory_key):
    """Return (complete inventory, used source retention), never a cached graph."""
    from . import backtest_fixed_lot_initial_recovery_reuse as initial
    if type(operation) is not initial._Initial or operation not in initial._ISSUED:
        raise ValueError('First-inventory source reader needs a genuine issued operation')
    operation.require()
    if initial._OPERATION.get() is not operation or initial._READ.get() is not operation:
        raise ValueError('First-inventory source reader is outside its exact owned scope')
    policy = selected_first_inventory_source_policy(
        operation.proof.owner.operation.source, operation.policy)
    if policy is None:
        return initial._cold_loader(loader), False
    if type(policy) is not FirstInventorySourceReusePolicy:
        raise ValueError('First-inventory source declaration type differs')
    codes, bindings = code_snapshot()
    if (any(item not in operation.codes[0] for item in codes)
            or bindings not in operation.codes[1]):
        raise ValueError('First-inventory source helper is outside its issued code guard')
    snapshot = operation.proof.normalized_snapshot
    if len(snapshot[4]) + len(snapshot[2]) > policy.max_contexts:
        initial._bypass(operation.proof.owner, 'first_inventory_source_context_budget')
        return initial._cold_loader(loader), False
    from . import backtest_fixed_lot_management_reuse as reuse
    active = reuse._ACTIVE.set(None)
    context = reuse._CONTEXT_OWNER.set(None)
    try:
        operation.require()
        result = loader()
        operation.require()
    finally:
        reuse._CONTEXT_OWNER.reset(context)
        reuse._ACTIVE.reset(active)
    # Qualification is after the complete read: cardinality must never be
    # reduced to fit a cache. An oversized source-retained result is not cold
    # evidence and therefore must be discarded before the independent reload.
    content = initial._image(result)
    private = initial._copy(result)
    if initial._image(result) != content or initial._image(private) != content:
        raise ValueError('First-inventory source result changed during budget qualification')
    size = initial._size(private) + initial._size(content) + initial._size(inventory_key)
    within_budget = (initial._nodes(private) <= operation.policy.max_inventory_rows
                     and size <= operation.policy.max_inventory_bytes)
    del private, content
    operation.require()
    if not within_budget:
        del result
        initial._bypass(operation.proof.owner, 'first_inventory_source_inventory_budget')
        result = initial._cold_loader(loader)
        operation.require()
        return result, False
    return result, True
