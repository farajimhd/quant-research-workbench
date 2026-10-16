"""Read-only native execution attribution for completed programme audits.

This joins actual normalized OMS requests and admissions. It cannot restore an
executable OMS, submit orders, or authorize restarting a completed broker run.
The ordinary OMS attribution validator remains the single identity authority.
"""
from dataclasses import dataclass, replace
from datetime import datetime
from types import MappingProxyType

from .qmd_columnar_history_transport import _owned_worker
from .typed_backtest_financial_review import load_typed_backtest_financial_evidence
from .backtest_v4_execution_restore import reconstruct_broker_executions
from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix, V4CommittedPrefix
from src.trading_runtime.arte_oms_projection import (
    load_latest_committed_oms_groups, load_committed_oms_admission_page,
)
from src.trading_runtime.arte_intent_projection import load_committed_strategy_intent_page
from src.trading_runtime.ibkr_schema import Execution
from src.trading_runtime.order_management import OrderManagementEngine
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy


def completed_program_audit_reader_factory(native_factory):
    """Use the existing bounded batched detail reader, retaining every seal.

    Each invocation still owns a fresh SELECT-only connection. This reduces
    per-family round trips; it neither caches a prefix nor skips row hashing.
    """
    if not callable(native_factory):
        raise ValueError('Completed audit requires its native connection factory')
    def owned():
        client = native_factory()
        client.v4_batched_detail_readback = True
        return client
    return owned


@dataclass(frozen=True)
class _AttributionGroup:
    account_id: str
    intent: object
    orders: tuple
    broker_order_request_indexes: object
    broker_order_roles: object


@dataclass(frozen=True)
class CommittedExecutionAttribution:
    """An inert normalized view implementing only the native attribution read.

    These fields match the existing validator's read protocol. No native OMS
    actor or private executable actor state is installed or impersonated.
    """
    _groups: object
    _group_by_broker_id: object

    def execution_attribution(self, execution):
        return OrderManagementEngine.execution_attribution(self, execution)


def _project_attribution(groups, admissions, sources, assignments):
    """Project only identities; full rule reselection belongs to the audit."""
    by_assignment = {row.assignment_id: row for row in assignments}
    if len(by_assignment) != len(assignments):
        raise ValueError('Completed attribution repeats an assignment')
    mapped, broker_groups, requests, broker_coids = {}, {}, {}, {}
    for state in groups:
        row = state.group
        source = sources.get(state.intent_record_id)
        admission = admissions.get(state.sequence)
        if source is None or admission is None:
            raise ValueError('Completed OMS group lacks verified intent/admission')
        assignment = by_assignment.get(admission['assignment_id'])
        identity = row['group_id']
        if (identity in mapped or assignment is None
                or assignment.account_id != row['account_id']
                or assignment.ticker != source.intent.ticker
                or assignment.strategy_id != row['strategy_id']
                or assignment.strategy_revision != row['strategy_revision']
                or source.account_id != row['account_id']
                or source.intent.intent_id != row['strategy_intent_id']
                or source.record_id != state.intent_record_id
                or not source.sequence < state.sequence
                or source.intent.metadata.get('assignment_id', assignment.assignment_id) != assignment.assignment_id):
            raise ValueError('Completed attribution differs from committed assignment/intent')
        intent = replace(source.intent, metadata={**source.intent.metadata,
            'assignment_id': assignment.assignment_id})
        indexes, roles = {}, {}
        for request in state.orders:
            if request.cOID in requests and requests[request.cOID] != request:
                raise ValueError('Completed OMS request identity conflicts')
            requests[request.cOID] = request
        for binding in state.broker_bindings:
            broker_id = str(binding['broker_order_id'])
            index = binding['request_index']
            if (not broker_id or broker_id in broker_groups
                    or index is not None and (type(index) is not int or not 0 <= index < len(state.orders))):
                raise ValueError('Completed OMS broker binding is ambiguous or incomplete')
            broker_groups[broker_id] = identity
            if index is not None:
                indexes[broker_id] = index
                broker_coids[broker_id] = state.orders[index].cOID
            if binding['has_role']:
                roles[broker_id] = binding['role']
        mapped[identity] = _AttributionGroup(row['account_id'], intent, state.orders,
            MappingProxyType(indexes), MappingProxyType(roles))
    return (CommittedExecutionAttribution(MappingProxyType(mapped), MappingProxyType(broker_groups)),
            requests, broker_coids)


async def read_completed_program_execution_evidence(reader_factory, prefix, *, actor,
        maximum_events=100000, maximum_transitions=20000, maximum_groups=2000):
    """Read exact actual fills/fees and native request lineage off the event loop.

    Full source/rule/policy replay remains mandatory in the caller. This API
    provides no policy proof and accepts only an immutable completed V4 prefix.
    """
    if (type(actor) is not AssignedProgramStrategy
            or type(prefix) is not V4CommittedPrefix or prefix.status != 'completed'
            or type(maximum_events) is not int or not 1 <= maximum_events <= 1000000
            or type(maximum_groups) is not int or not 1 <= maximum_groups <= 4096
            or type(maximum_transitions) is not int or not 500 <= maximum_transitions <= 100000
            or prefix.last_sequence > maximum_events):
        raise ValueError('Completed execution evidence requires bounded terminal authority')
    assignments = actor.assignments()
    def read():
        client = reader_factory()
        try:
            if client.execute("SELECT getSetting('readonly')").strip() != '1':
                raise ValueError('Completed execution evidence requires SELECT-only credentials')
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError('Completed execution prefix moved before read')
            financial = load_typed_backtest_financial_evidence(client, prefix.run_id,
                page_size=500, maximum_rows=maximum_events)
            groups = load_latest_committed_oms_groups(client, prefix,
                allowed_accounts=frozenset(row.account_id for row in assignments),
                strategy_identity=(actor.strategy_id, actor.revision), require_tactic=True,
                max_transitions=maximum_transitions, exact_prices=True)
            if len(groups) > maximum_groups:
                raise ValueError('Completed execution evidence exceeds its group bound')
            admissions = (load_committed_oms_admission_page(client, prefix, groups,
                max_rows=maximum_groups) if groups else {})
            identifiers = {row.intent_record_id for row in groups}
            if None in identifiers:
                raise ValueError('Completed OMS group lacks source intent identity')
            wanted = sorted(identifiers)
            sources = {}
            for offset in range(0, len(wanted), 500):
                chunk = tuple(wanted[offset:offset + 500])
                page = load_committed_strategy_intent_page(client, prefix,
                    record_ids=chunk, limit=len(chunk), include_source_batch=True)
                if len(page) != len(chunk) or {row.record_id for row in page} != set(chunk):
                    raise ValueError('Completed OMS intent join is incomplete')
                sources.update((row.record_id, row) for row in page)
            attribution, requests, broker_coids = _project_attribution(
                groups, admissions, sources, assignments)
            rows = reconstruct_broker_executions(financial['fills'], financial['commissions'],
                requests_by_coid=requests, coid_by_broker_id=broker_coids,
                next_execution_id=len(financial['fills']) + 1)
            executions = tuple(Execution(**{**row, 'trade_time': datetime.fromisoformat(row['trade_time'])}) for row in rows)
            for execution in executions:
                attribution.execution_attribution(execution)
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError('Completed execution prefix moved during read')
            return executions, attribution
        finally:
            client.close()
    return await _owned_worker(read)
