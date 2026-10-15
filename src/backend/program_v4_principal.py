"""Shared normalized program writer ownership and reviewed principal plan.

No credentials, schema operations, run creation or broker orders occur here.
The existing V4 physical commit protocol is reused under its own run owner.
"""
from dataclasses import dataclass
from hashlib import sha256
import re

from .live_assignment_base_keeper import KeeperAssignmentHead
from .live_strategy_one_v4_principal import LiveV4KeeperLease, LiveV4WriterClient, LiveV4PrincipalPlan
from src.trading_runtime.keeper_session import ManagedKeeperSession
from src.trading_runtime.strategy_registry import ProgramStrategyExecutorRegistration, program_strategy_executor


PRINCIPAL = 'program_v4_runner'


class _ProgramRunOwner(KeeperAssignmentHead):
    @staticmethod
    def path(run_id):
        if type(run_id) is not str or not 1 <= len(run_id) <= 256 or any(c in run_id for c in '\r\n\0'):
            raise ValueError('Program run owner requires bounded exact identity')
        return '/trading/program-v4/v1/' + sha256(run_id.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ProgramV4KeeperLease(LiveV4KeeperLease):
    registration: object
    configuration_hash: str

    @classmethod
    def acquire(cls, session, *, run_id, owner_id, registration, configuration_hash):
        if (type(session) is not ManagedKeeperSession
                or type(registration) is not ProgramStrategyExecutorRegistration
                or program_strategy_executor(*registration.key) is not registration
                or re.fullmatch('[0-9a-f]{64}', configuration_hash or '') is None):
            raise ValueError('Program owner requires installed executable and approved configuration identity')
        registration.verify()
        owner = _ProgramRunOwner(session)
        epoch = owner.acquire(run_id, owner_id=owner_id)
        if epoch is None:
            raise RuntimeError('Program run is owned by another process')
        return cls(owner, run_id, owner_id, epoch, registration, configuration_hash)

    def assert_current(self):
        super(ProgramV4KeeperLease, self).assert_current()
        if program_strategy_executor(*self.registration.key) is not self.registration:
            raise RuntimeError('Program run executable pins changed')


def desired_plan():
    from . import live_strategy_one_v4_principal as live
    from src.trading_runtime.arte_journal_writer import _FAMILIES, _v4_family_table
    from src.trading_runtime.arte_journal_schema import V4_COMMIT_TABLES, MARKET_READ_TABLES
    from src.trading_runtime.rule_decision_basis_schema import STAGED_TABLES
    from src.trading_runtime.trading_price_contracts import program_exact_price_storage_contracts
    from src.trading_runtime.arte_oms_observation_snapshot import TABLES as OBSERVATIONS
    from src.trading_runtime.arte_portfolio_control_v4 import CONTROL
    from src.trading_runtime.arte_broker_policy_v4 import POLICY_EVENT, MESSAGE
    from src.trading_runtime import columnar_strategy_schema as release_schema
    from src.trading_runtime import strategy_configuration_schema as selection_schema
    from src.trading_runtime.configuration_execution_binding import TABLES as BINDING
    from src.trading_runtime.configuration_execution_risk import TABLES as RISK
    base = {_v4_family_table(name) for name, _, _, _ in _FAMILIES if name not in live._EXCLUDED_FAMILY}
    details = live._V4_LIVE_DETAIL - {'trading_strategy_one_entry_evidence_v1', 'trading_strategy_one_add_evidence_v1'}
    exact = {t.name for t in program_exact_price_storage_contracts() if 'backtest' not in t.name}
    writable = frozenset(base | {t.name for t in V4_COMMIT_TABLES} | details | live._PORTFOLIO_WRITE
        | live._CONTEXT_WRITE | exact | {t.name for t in (*STAGED_TABLES, *OBSERVATIONS, CONTROL, POLICY_EVENT, MESSAGE)})
    shared_observation_headers = {t.name for t in OBSERVATIONS}
    if any('backtest' in name or name.startswith('trading_strategy_one_')
           and name not in shared_observation_headers for name in writable):
        raise RuntimeError('Program production plan escaped shared normalized families: ' + ','.join(
            sorted(n for n in writable if 'backtest' in n or n.startswith('trading_strategy_one_'))))
    reference = {t.name for t in (*release_schema.TABLES, *selection_schema.TABLES, *BINDING, *RISK)}
    return LiveV4PrincipalPlan(PRINCIPAL, writable | live._POLICY_READ | MARKET_READ_TABLES | reference
        | live._LEGACY_COMMIT_READ,
        writable, frozenset({'storage_policies', 'tables', 'columns', 'parts', 'data_skipping_indices'}),
        frozenset({('q_live', 'events'), ('q_live', 'market_stock_split_v1')}))


def storage_contracts():
    from .live_strategy_one_v4_principal import _LEGACY_COMMIT_READ
    from src.trading_runtime.arte_journal_writer import _CONTRACTS
    from src.trading_runtime import columnar_strategy_schema as release_schema
    from src.trading_runtime import strategy_configuration_schema as selection_schema
    from src.trading_runtime.configuration_execution_binding import TABLES as BINDING
    from src.trading_runtime.configuration_execution_risk import TABLES as RISK
    contracts = dict(_CONTRACTS)
    contracts.update({t.name: t for t in (*release_schema.TABLES, *selection_schema.TABLES, *BINDING, *RISK)})
    plan = desired_plan()
    from src.trading_runtime.arte_journal_schema import MARKET_READ_TABLES
    # Legacy commit markers are read-only mixed-version guards. Their minimal
    # version readers own compatibility; current schemas must not be imposed
    # on those historical tables (the existing live V4 preflight does likewise).
    names = plan.insert_arte | (plan.select_arte - plan.insert_arte - MARKET_READ_TABLES - _LEGACY_COMMIT_READ)
    missing = names - set(contracts)
    if missing:
        raise RuntimeError('Program principal lacks typed storage contracts: ' + ','.join(sorted(missing)))
    return tuple(contracts[name] for name in sorted(names))


def program_v4_preflight(client):
    from src.trading_runtime.arte_journal_schema import storage_preflight, journal_permission_preflight, MARKET_READ_TABLES
    if type(client) is not ProgramV4WriterClient:
        raise TypeError('Program writer requires its native owned client')
    client.live_v4_lease.assert_current()
    plan = desired_plan()
    if client.execute('SELECT currentUser()').strip() != PRINCIPAL:
        raise RuntimeError('Program writer authenticated as another principal')
    storage_preflight(client, tables=storage_contracts())
    journal_permission_preflight(client, journal_tables=plan.insert_arte,
        read_only_tables=plan.select_arte - plan.insert_arte - MARKET_READ_TABLES,
        reference_read_tables=plan.select_reference, allow_live_event_replay=True)
    client.live_v4_lease.assert_current()


class ProgramV4WriterClient(LiveV4WriterClient):
    def __init__(self, raw, lease):
        if type(lease) is not ProgramV4KeeperLease:
            raise TypeError('Program client requires its own installed-program run lease')
        lease.assert_current()
        super().__init__(raw, lease)
        from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
        from src.trading_runtime.arte_portfolio_sync_dispatch import PortfolioSyncDispatch
        self.typed_insert_dispatch = TypedInsertDispatch(lease.owner._session.client)
        self.typed_sync_insert_dispatch = PortfolioSyncDispatch(lease.owner._session.client)
        self.base_url = getattr(raw, 'base_url', None)
        self.user = getattr(raw, 'user', None)

    def insert_tables(self):
        return desired_plan().insert_arte

    def execute_registered_signal_insert(self, *args, **kwargs):
        raise RuntimeError('Program signals require their shared Signal Stream publisher owner')


async def open_program_v4_writer(lease, *, query_limits):
    """Open the managed owned writer off-loop; caller owns a successful client.

    This narrow seam never exposes the runner as a generic publication role.
    Exact grants, normalized storage placement and actual lease are audited.
    """
    from .program_app_credentials import _open_managed_client, _bounded_query_settings
    from .qmd_columnar_history_transport import _owned_worker
    if type(lease) is not ProgramV4KeeperLease:
        raise TypeError('Program writer opening requires its concrete installed-program lease')
    settings = _bounded_query_settings(query_limits)
    opened = []
    def owned():
        lease.assert_current()
        raw = _open_managed_client(PRINCIPAL,
            timeout_seconds=settings['max_execution_time'] + 30, settings=settings)
        try:
            client = ProgramV4WriterClient(raw, lease)
            program_v4_preflight(client)
            lease.assert_current()
            opened.append(client)
            return client
        except BaseException:
            raw.close()
            raise
    try:
        return await _owned_worker(owned)
    except BaseException:
        if opened:
            await _owned_worker(opened[0].close)
        raise

