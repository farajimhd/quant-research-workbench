"""Managed assembly of the existing programme journal's explicit authorities."""
import asyncio
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

from .backtest_market_data import CertifiedMarketDayPlan
from .backtest_fixed_market_authority import _validate_plans
from .backtest_program_journal_startup import _settle_owned_task
from src.market_engine.columnar_history_product import require_hash
from src.trading_runtime.arte_journal_commit_v4 import MAX_V4_COMMIT_EVENTS


@dataclass(frozen=True)
class ManagedProgramJournalRequest:
    market_parent: CertifiedMarketDayPlan
    market_execution: CertifiedMarketDayPlan
    liquidity_plan_hash: str
    batch_size: int = 1024
    queue_capacity: int = 8

    def __post_init__(self):
        if (type(self.market_parent) is not CertifiedMarketDayPlan
                or type(self.market_execution) is not CertifiedMarketDayPlan
                or type(self.batch_size) is not int or not 1 <= self.batch_size <= MAX_V4_COMMIT_EVENTS
                or type(self.queue_capacity) is not int or not 1 <= self.queue_capacity <= 64):
            raise ValueError('Managed programme journal requires native bounded source authority')
        require_hash(self.liquidity_plan_hash)
        _validate_plans(self.market_parent, self.market_execution)


def prepare_managed_journal_contracts(controller, request):
    """Local native projections before ownership or database connections open."""
    from .replay_run_service import ReplayRunController, _simulation_config
    from .backtest_program_journal_bootstrap import _configuration_hash
    from .backtest_v4_run_context import historical_runtime_config
    from .backtest_journal_clickhouse import backtest_code_hash
    from src.trading_runtime.backtest_broker_contract import project_broker_contract, BacktestBrokerContract
    from src.trading_runtime.backtest_journal_price_contract import (
        project_journal_price_contract, BacktestJournalPriceContract, EXACT_PRICE_CODEC)
    if type(controller) is not ReplayRunController or type(request) is not ManagedProgramJournalRequest:
        raise TypeError('Managed journal needs its native programme controller and request')
    actor = controller._program_strategy
    definition = controller.definition
    if (actor is None or controller._runtime is not None or controller._journal is not None
            or definition.market_data_plan.get('token') != request.market_parent.token
            or request.market_parent.sessions != (definition.session_date.isoformat(),)
            or set(request.market_execution.tickers) != {row.ticker for row in actor.assignments()}):
        raise ValueError('Managed journal differs from its fresh controller source scope')
    code_hash = backtest_code_hash(Path(__file__).resolve().parents[2])
    if code_hash != actor.program_release.header['code_hash']:
        raise ValueError('Managed programme release differs from the native runtime source closure')
    accounts = tuple(dict.fromkeys(row.account_id for row in actor.assignments()))
    runtime_config = historical_runtime_config(mode=definition.mode,
        configuration=definition.configuration_revision['payload'], account_ids=accounts,
        anchor_date=definition.session_date, run_id=controller.run_id)
    simulation = replace(_simulation_config(definition),
        liquidity_resolution_ms=controller._program_liquidity_resolution_ms)
    broker = BacktestBrokerContract(project_broker_contract(controller.run_id, simulation,
        liquidity_plan_hash=request.liquidity_plan_hash), definition.initial_cash)
    price = BacktestJournalPriceContract(project_journal_price_contract(run_id=controller.run_id,
        run_month=definition.session_date.replace(day=1), configuration_hash=_configuration_hash(definition),
        price_codec=EXACT_PRICE_CODEC))
    return dict(runtime_config=runtime_config, expected_release_hash=actor.program_release.header['content_hash'],
        code_hash=code_hash, fixed_market_parent_plan=request.market_parent,
        fixed_market_execution_plan=request.market_execution, journal_price_contract=price,
        expected_journal_price_contract_hash=price.row['content_hash'], broker_contract=broker,
        expected_broker_contract_hash=broker.row['content_hash'], liquidity_plan_hash=request.liquidity_plan_hash,
        batch_size=request.batch_size, queue_capacity=request.queue_capacity)


class ManagedProgramJournalScope(Mapping):
    """Immutable call scope with an explicit, verified ownership handoff."""
    def __init__(self, values, controller):
        self._values = MappingProxyType(dict(values))
        self.controller = controller
        self.adopted = False

    def __getitem__(self, key):
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def mark_adopted(self):
        controller = self.controller
        if (controller._fixed_keeper_session is not self['keeper_session']
                or controller._fixed_v4_lease is not self['writer_client'].backtest_v4_lease
                or controller._journal_writer is None
                or controller._journal_writer._client is not self['writer_client']):
            raise RuntimeError('Managed journal lacks exact controller adoption')
        self.adopted = True


@asynccontextmanager
async def managed_program_journal_scope(controller, request, *, contracts):
    """Open after input preparation; dispose controls, transfer run owners only.

    Operational authorization is required before invocation. No schema creation,
    source producer, backtest admission or task start occurs in this assembly.
    """
    from src.trading_runtime.keeper_session import open_workstation_keeper_session
    from src.trading_runtime.keeper_ownership import KeeperOwnershipCoordinator
    from src.trading_runtime.arte_journal_writer import (backtest_v4_context_client_from_env,
        backtest_v4_operator_client_from_env, backtest_v4_journal_client_from_env)
    from .backtest_v4_keeper_lease import BacktestV4KeeperLease
    from .backtest_fixed_v4_certification import certify_strategy_one_v4_projection
    owned, scope = {}, None
    def close():
        errors = []
        adopted = scope is not None and scope.adopted
        for name in ('terminal_client', 'read_client', 'context_client'):
            resource = owned.pop(name, None)
            if resource is not None:
                try: resource.close()
                except BaseException as error: errors.append(error)
        coordinator, definition_lease = owned.pop('keeper', None), owned.pop('definition_lease', None)
        if definition_lease is not None:
            try:
                coordinator.release_portfolio_admission_lease('backtest-definition:' + controller.run_id,
                    owner_id=definition_lease['owner_id'], epoch=definition_lease['epoch'])
            except BaseException as error: errors.append(error)
        if not adopted:
            for name, method in (('writer_client', 'close'), ('lease', 'release'), ('keeper_session', 'close')):
                resource = owned.pop(name, None)
                if resource is not None:
                    try: getattr(resource, method)()
                    except BaseException as error: errors.append(error)
        owned.clear()
        if errors:
            raise BaseExceptionGroup('Managed programme control cleanup failed', errors)
    def open_owned():
        try:
            session = owned['keeper_session'] = open_workstation_keeper_session()
            lease = owned['lease'] = BacktestV4KeeperLease.acquire(session,
                run_id=controller.run_id, owner_id='program-backtest-' + uuid4().hex)
            owned['context_client'] = backtest_v4_context_client_from_env(keeper_session=session)
            owned['read_client'] = backtest_v4_operator_client_from_env(program_decisions=True)
            owned['writer_client'] = backtest_v4_journal_client_from_env(
                keeper_session=session, lease=lease, program_decisions=True)
            owned['terminal_client'] = backtest_v4_operator_client_from_env(program_decisions=True)
            keeper = owned['keeper'] = KeeperOwnershipCoordinator(session.client)
            claim = owned['definition_lease'] = keeper.acquire_portfolio_admission_lease(
                'backtest-definition:' + controller.run_id, owner_id='definition-' + uuid4().hex, ttl_seconds=300.)
            if claim is None:
                raise RuntimeError('Programme definition is already owned')
            values = dict(contracts, **{key: owned[key] for key in ('context_client', 'read_client',
                'writer_client', 'terminal_client', 'keeper_session', 'keeper', 'definition_lease')},
                attempt_id=str(uuid4()), projection_certifier=certify_strategy_one_v4_projection)
            return ManagedProgramJournalScope(values, controller)
        except BaseException as error:
            try: close()
            except BaseException as cleanup:
                raise BaseExceptionGroup('Managed programme opening and cleanup failed', [error, cleanup])
            raise
    task = asyncio.create_task(asyncio.to_thread(open_owned))
    failure = None
    try:
        scope = await asyncio.shield(task)
        yield scope
    except BaseException as error:
        failure = error
        if isinstance(error, asyncio.CancelledError):
            try: scope = await _settle_owned_task(task)
            except BaseException as opening_error:
                if opening_error is not error:
                    raise BaseExceptionGroup('Managed opening cancellation failed', [error, opening_error])
        raise
    finally:
        try: await _settle_owned_task(asyncio.create_task(asyncio.to_thread(close)))
        except BaseException as cleanup:
            if failure is not None:
                raise BaseExceptionGroup('Managed preparation and cleanup failed', [failure, cleanup])
            raise
