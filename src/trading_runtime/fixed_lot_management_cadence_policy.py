"""Declared structural management clock; broker and inherited exit clocks stay unchanged."""
from dataclasses import dataclass
RULE = 'fixed-lot-structural-management-cadence@1'
PARAMETER = 'management_decision_interval_ms'
@dataclass(frozen=True, slots=True)
class FixedLotManagementCadencePolicy:
    interval_ms: int
    def __post_init__(self):
        if type(self.interval_ms) is not int or not 100 <= self.interval_ms <= 30000 or self.interval_ms % 100:
            raise ValueError('Management cadence requires a positive 100ms multiple through 30000ms')
    def payload(self): return self.interval_ms

def require_declared_management_cadence(release,policy):
    selected = RULE in release.rule_set_contracts
    if not selected:
        if policy is not None: raise ValueError('Undeclared management cadence')
        return None
    if type(policy) is not FixedLotManagementCadencePolicy: raise ValueError('Exact declared management cadence required')
    policy.__post_init__()
    return policy

def parse_declared_management_cadence(release,value):
    if RULE not in release.rule_set_contracts:
        if value is not None: raise ValueError('Orphan management cadence parameter')
        return None
    return require_declared_management_cadence(release,FixedLotManagementCadencePolicy(value))

def recovery_cadence_binding(client):
    """Small semantic lineage trigger; this never grants recovery authority."""
    from src.backend.backtest_fixed_structural_lot_management import FixedStructuralLotRecoveryContext
    from .fixed_structural_lot_cold_recovery import FixedStructuralLotColdRecoveryContext
    rows=getattr(client,'fixed_lot_recovery_contexts',())
    if type(rows) is not tuple or len(rows)>100000:
        raise ValueError('Cadence recovery inventory is unbounded')
    result=[]
    for row in rows:
        if type(row) is not tuple or len(row)!=2 or type(row[0]) is not str:
            raise ValueError('Cadence recovery binding is malformed')
        batch,context=row
        if type(context) is FixedStructuralLotRecoveryContext:
            signature=(context.original_record_id,context.original_batch_id,
                context.original_sequence,context.through_sequence)
        elif type(context) is FixedStructuralLotColdRecoveryContext:
            signature=(context.batch_id,context.before_sequence,context.graph_json)
        else: raise ValueError('Cadence recovery context has foreign type')
        result.append((batch,id(context),signature))
    return tuple(result)

def execution_cadence_binding(runtime,owner,key):
    """Observe bounded current OMS semantics, never wall time or prefix movement."""
    from .order_management import OrderManagementEngine,_ManagedOrderGroup
    from src.backend.backtest_fixed_structural_lot_management import _small_tree
    from .journal_contract import canonical_json
    manager=runtime.order_manager
    if type(manager) is not OrderManagementEngine:
        raise ValueError('Cadence requires the actual native OMS engine')
    source=owner.operation.source
    if (manager.run_id!=runtime.run_id or manager.run_id!=source.run_id
            or manager.strategy_id!=runtime.config.strategy_id
            or manager.strategy_revision!=runtime.config.strategy_revision
            or manager.journal is not runtime.journal):
        raise ValueError('Cadence OMS engine has foreign run/strategy/journal ownership')
    group_id=owner.groups[key]
    group=manager._groups.get(group_id)
    if (type(group) is not _ManagedOrderGroup or group.group_id!=group_id
            or (group.account_id,group.intent.metadata.get('assignment_id'),group.intent.ticker)!=key
            or group.intent.intent_id!=owner.entries[key].intent.intent_id):
        raise ValueError('Cadence OMS group differs from the held owner')
    if len(group.orders)>128 or len(group.broker_order_state_fingerprints)>128:
        raise ValueError('Cadence current protected group is unbounded')
    from .ibkr_schema import OrderRequest
    if any(type(order) is not OrderRequest for order in group.orders):
        raise ValueError('Cadence group has foreign order requests')
    orders=tuple((o.acctId,o.conid,o.cOID,o.parentId,o.orderType,o.side,o.quantity,o.cashQty,
        o.price,o.auxPrice,o.trailingAmt,o.trailingType) for o in group.orders)
    return canonical_json(_small_tree((group.state,group.filled_quantity,group.remaining_quantity,
        group.protection_required_quantity,group.protection_coverage_quantity,group.rejection_reason,
        orders,group.broker_order_roles,group.broker_order_slices,
        group.broker_order_request_indexes,group.filled_by_broker_order,
        group.broker_order_state_fingerprints,frozenset(group.terminal_broker_order_ids))))
