"""Operator-pinned startup of the existing app; read-only central admission.

The bounded runtime file contains approval references, not trading definitions
or state. Database seals remain authoritative. No publication or run starts here.
"""
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import re
from types import MappingProxyType
from uuid import UUID

from fastapi import FastAPI
from .strategy_configuration_reader import _unique_object, _reject_constant
from .qmd_columnar_history_transport import _owned_worker
from .backtest_program_journal_startup import _settle_owned_task
from .program_app_credentials import program_app_reader_factory
from .program_configuration_selection_service import ProgramConfigurationSelectionService
from src.trading_runtime.strategy_registry import ProgramStrategyExecutorRegistration, register_program_strategy_executor
from src.trading_runtime.columnar_strategy_reader import load_program_release
from src.trading_runtime.keeper_session import open_workstation_keeper_session
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch

SPEC_KEY = 'approved_managed_program_startup'
OWNED_KEY = 'owned_program_policy_session'


def configure_managed_program_startup(application, path, expected_sha256):
    from .program_app_startup import _STATE_KEY, program_configuration_selection_service
    if (type(application) is not FastAPI or type(path) is not str or not path
            or type(expected_sha256) is not str or re.fullmatch('[0-9a-f]{64}', expected_sha256) is None):
        raise ValueError('Managed programme startup requires explicit native app and approval file hash')
    spec = (path, expected_sha256)
    prior = getattr(application.state, SPEC_KEY, None)
    if (prior not in (None, spec) or getattr(application.state, _STATE_KEY, None) is not None
            or program_configuration_selection_service() is not None):
        raise ValueError('Managed programme startup cannot replace an app authority')
    setattr(application.state, SPEC_KEY, spec)
    return application


def read_startup_plan(path, expected_sha256):
    selected = Path(path).resolve()
    if not selected.is_relative_to(Path('D:/TradingML/runtimes').resolve()):
        raise ValueError('Programme startup approval must remain in the runtime root')
    with selected.open('rb') as stream:
        raw = stream.read(128 * 1024 + 1)
    if len(raw) > 128 * 1024 or sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Programme startup approval differs from its bounded file hash')
    plan = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    version3 = type(plan) is dict and plan.get('contract') == 'approved-program-app-startup-v3'
    version2 = type(plan) is dict and plan.get('contract') in (
        'approved-program-app-startup-v2', 'approved-program-app-startup-v3')
    expected = {'contract', 'approved_selections', 'executors', 'concurrency'}
    if version2:
        expected.add('approved_execution_bindings')
    if version3:
        expected.add('approved_execution_risks')
    if (type(plan) is not dict or set(plan) != expected
            or plan['contract'] not in ('approved-program-app-startup-v1', 'approved-program-app-startup-v2',
                                        'approved-program-app-startup-v3')
            or type(plan['concurrency']) is not int or not 1 <= plan['concurrency'] <= 6
            or type(plan['approved_selections']) is not dict or not 1 <= len(plan['approved_selections']) <= 256
            or type(plan['executors']) is not list or not 1 <= len(plan['executors']) <= 256):
        raise ValueError('Programme startup plan lacks its explicit bounded approval references')
    pins = plan['approved_selections']
    if any(type(k) is not str or str(UUID(k)) != k or type(v) is not str
            or re.fullmatch('[0-9a-f]{64}', v) is None for k, v in pins.items()):
        raise ValueError('Programme startup selection pins are invalid')
    execution_pins = plan.get('approved_execution_bindings', {})
    if version2 and (type(execution_pins) is not dict or set(execution_pins) != set(pins)
            or any(type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None
                for value in execution_pins.values())):
        raise ValueError('Programme startup requires complete approved execution binding pins')
    risk_pins = plan.get('approved_execution_risks', {})
    if version3 and (type(risk_pins) is not dict or not risk_pins or set(risk_pins) - set(execution_pins)
            or any(type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None
                for value in risk_pins.values())):
        raise ValueError('Programme startup execution risk approvals differ')
    executors = []
    for row in plan['executors']:
        executor_fields = {'strategy_id', 'revision', 'expected_content_hash',
                           'expected_code_hash', 'expected_winner_hash'}
        if version3:
            executor_fields.add('requires_execution_risk')
        if type(row) is not dict or set(row) != executor_fields:
            raise ValueError('Programme executable approval has unknown fields')
        executor = ProgramStrategyExecutorRegistration(**row)
        executor.verify()
        executors.append(executor)
    if len({p.key for p in executors}) != len(executors):
        raise ValueError('Programme startup repeats an executable identity')
    result = MappingProxyType(dict(pins)), tuple(executors), plan['concurrency']
    if version3:
        return (*result, MappingProxyType(dict(execution_pins)), MappingProxyType(dict(risk_pins)))
    return (*result, MappingProxyType(dict(execution_pins))) if version2 else result


async def _open_owned_session():
    task = asyncio.create_task(asyncio.to_thread(open_workstation_keeper_session))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        session = await _settle_owned_task(task)
        await _settle_owned_task(asyncio.create_task(asyncio.to_thread(session.close)))
        raise


async def prepare_managed_program_startup(application, spec):
    """Cold verify releases before code registration; own only policy read session."""
    from .program_app_startup import configure_program_app_startup
    if getattr(application.state, OWNED_KEY, None) is not None:
        raise RuntimeError('Managed programme startup already owns a policy session')
    approved = await _owned_worker(read_startup_plan, *spec)
    pins, registrations, concurrency = approved[:3]
    execution_pins = approved[3] if len(approved) >= 4 else {}
    risk_pins = approved[4] if len(approved) >= 5 else {}
    iterator = iter(registrations)
    async def worker():
        while True:
            registration = next(iterator, None)
            if registration is None:
                return
            release = await load_program_release(program_app_reader_factory('release_reader'),
                strategy_number=registration.revision, expected_content_hash=registration.expected_content_hash,
                expected_code_hash=registration.expected_code_hash, expected_winner_hash=registration.expected_winner_hash)
            if (release.header['strategy_id'], release.header['strategy_number']) != registration.key:
                raise ValueError('Programme startup release differs from approved executable identity')
    async with asyncio.TaskGroup() as tasks:
        for _ in range(min(concurrency, len(registrations))):
            tasks.create_task(worker())
    if getattr(application.state, SPEC_KEY, None) != spec:
        raise RuntimeError('Programme startup approval owner changed during cold reads')
    session = await _open_owned_session()
    setattr(application.state, OWNED_KEY, (session, None))
    try:
        if getattr(application.state, SPEC_KEY, None) != spec or not session.writable:
            raise RuntimeError('Programme policy session or approval owner changed')
        for registration in registrations:
            register_program_strategy_executor(registration)
        service = ProgramConfigurationSelectionService(approved_selections=pins,
            selection_client_factory=program_app_reader_factory('selection_reader'),
            release_client_factory=program_app_reader_factory('release_reader'),
            policy_client_factory=program_app_reader_factory('market_input_reader'),
            policy_dispatch=TypedInsertDispatch(session.client), concurrency=concurrency,
            approved_execution_bindings=execution_pins, approved_execution_risks=risk_pins)
        configure_program_app_startup(application, service)
        setattr(application.state, OWNED_KEY, (session, service))
        return service
    except BaseException:
        await close_managed_program_startup(application)
        raise


async def close_managed_program_startup(application):
    """Drain this app's unused native handoff before closing its policy reader."""
    owned = getattr(application.state, OWNED_KEY, None)
    if owned is None:
        return
    setattr(application.state, OWNED_KEY, None)
    async def close_owned():
        errors = []
        if owned[1] is not None:
            try:
                await owned[1].close_prepared_handoff()
            except BaseException as error:
                errors.append(error)
        try:
            await asyncio.to_thread(owned[0].close)
        except BaseException as error:
            errors.append(error)
        if errors:
            raise BaseExceptionGroup('Managed programme shutdown failed', errors)
    await _settle_owned_task(asyncio.create_task(close_owned()))


def create_app():
    """Uvicorn factory uses the existing app and defers all I/O to lifespan."""
    import os
    from .app import app
    return configure_managed_program_startup(app,
        os.environ.get('PROGRAM_APP_STARTUP_PLAN', ''),
        os.environ.get('PROGRAM_APP_STARTUP_PLAN_SHA256', ''))
