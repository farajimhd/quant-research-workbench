"""Read-only admission of current app configuration references.

Selections, programme releases and portfolio policies retain independent
central authorities. This resolves them, but never publishes or starts a run.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
import json
import re

from .strategy_configuration_reader import load_configuration_selection
from .strategy_configuration_reader import _unique_object, _reject_constant
from .strategy_configuration_materialization import materialize_program_configuration
from .qmd_columnar_history_transport import _owned_worker
from .backtest_program_journal_startup import _settle_owned_task
from src.trading_runtime.columnar_strategy_reader import load_program_release
from src.trading_runtime import strategy_configuration_schema as schema
from src.trading_runtime.strategy_registry import program_strategy_executor
from src.trading_runtime.arte_portfolio_policy import load_attested_portfolio_policy
from src.trading_runtime.arte_journal_schema import TABLES, POLICY_ALLOWED_TABLES, storage_preflight
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
from src.trading_runtime.strategy_configuration_selection import restore_configuration_selection


@dataclass(frozen=True, slots=True)
class CertifiedProgramConfiguration:
    selection: object
    release: object
    revision: object
    execution_binding: object = None


class _BoundedPolicyReader:
    """Keep the existing policy codec while bounding its cold SELECT responses."""
    def __init__(self, client):
        self.client = client

    def execute(self, query):
        match = re.fullmatch(r"SELECT ([A-Za-z0-9_,]+) FROM arte\.([a-z0-9_]+) "
                             r"WHERE policy_hash='([0-9a-f]{64})' FORMAT JSONEachRow", query)
        allowed = {table for table, _ in POLICY_ALLOWED_TABLES.values()}
        if match is None or match[2] not in allowed | {"trading_portfolio_policy_v1", "trading_portfolio_policy_commit_v2"}:
            raise ValueError("Programme policy reader requires its defined catalogue SELECT")
        limit = 65535 if match[2] in allowed else 1
        raw = self.client.execute(query.removesuffix("FORMAT JSONEachRow") +
                                  f"LIMIT {limit + 1} FORMAT JSONEachRow")
        if type(raw) is not str or len(raw.encode("utf-8")) > 8 * 1024 * 1024:
            raise ValueError("Programme policy response exceeds bounded bytes")
        lines = tuple(line for line in raw.splitlines() if line.strip())
        if len(lines) > limit:
            raise ValueError("Programme policy response exceeds bounded rows")
        for line in lines:
            if type(json.loads(line, object_pairs_hook=_unique_object, parse_constant=_reject_constant)) is not dict:
                raise ValueError("Programme policy response is not a typed row")
        return raw


async def load_program_configuration_references(selected, *,
        release_client_factory, policy_client_factory, policy_dispatch, concurrency):
    """Verify programme/policy references for an already verified selection packet.

    Publication can use this before the selection itself is sealed in storage.
    This does not attest selection publication or authorize a run.
    """
    selected = restore_configuration_selection(selected.rows)
    if (any(not callable(factory) for factory in (release_client_factory, policy_client_factory))
            or type(policy_dispatch) is not TypedInsertDispatch
            or type(concurrency) is not int or not 1 <= concurrency <= 6):
        raise ValueError("Programme configuration needs bounded central readers and policy attestation")
    lock, connections = Lock(), []
    def distinct(factory):
        def owned():
            client = factory()
            with lock:
                if any(client is prior for prior in connections):
                    raise ValueError("Configuration authority reads cannot reuse an owned connection")
                connections.append(client)
            return client
        return owned
    release_factory = distinct(release_client_factory)
    policy_factory = distinct(policy_client_factory)
    header = selected.header
    registration = program_strategy_executor(header["strategy_id"], header["strategy_revision"])
    if registration.expected_content_hash != header["program_release_hash"]:
        raise ValueError("Configuration programme differs from installed executable pins")
    hashes = sorted({row["policy_hash"] for row in selected.rows[schema.ACCOUNT.name]})
    policy_names = {"trading_portfolio_policy_v1", "trading_portfolio_policy_commit_v2"}
    policy_names.update(table for table, _ in POLICY_ALLOWED_TABLES.values())
    policy_tables = tuple(table for table in TABLES if table.name in policy_names)
    def policies():
        def read(digest):
            client = policy_factory()
            try:
                if client.execute("SELECT getSetting('readonly')").strip() != "1":
                    raise ValueError("Programme policy reader must be SELECT-only")
                storage_preflight(client, tables=policy_tables)
                policy = load_attested_portfolio_policy(_BoundedPolicyReader(client), policy_dispatch, digest)
                if policy is None:
                    raise ValueError("Configuration portfolio policy is not centrally sealed")
                return policy
            finally:
                client.close()
        result, errors = {}, []
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="configuration-policy") as pool:
            futures = [(digest, pool.submit(read, digest)) for digest in hashes]
            for digest, future in futures:
                try:
                    result[digest] = future.result()
                except BaseException as exc:
                    errors.append(exc)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Configuration policy reads failed", errors)
        return result
    job = asyncio.ensure_future(asyncio.gather(
        load_program_release(release_factory, strategy_number=registration.revision,
            expected_content_hash=registration.expected_content_hash,
            expected_code_hash=registration.expected_code_hash,
            expected_winner_hash=registration.expected_winner_hash),
        _owned_worker(policies), return_exceptions=True))
    try:
        results = await asyncio.shield(job)
    except asyncio.CancelledError:
        await _settle_owned_task(job)
        raise
    errors = [result for result in results if isinstance(result, BaseException)]
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup("Configuration reference reads failed", errors)
    release, verified_policies = results
    if program_strategy_executor(*registration.key) is not registration:
        raise ValueError("Configuration installed pins changed during reference admission")
    revision = await _owned_worker(materialize_program_configuration, selected, release, verified_policies)
    if program_strategy_executor(*registration.key) is not registration:
        raise ValueError("Configuration installed pins changed during materialization")
    return CertifiedProgramConfiguration(selected, release, revision)


async def load_certified_program_configuration(*, selection_client_factory,
        release_client_factory, policy_client_factory, policy_dispatch,
        configuration_id, expected_content_hash, concurrency, expected_execution_binding_hash=None):
    """Verify persisted selection and settle every owned reference read."""
    if any(not callable(factory) for factory in (selection_client_factory, release_client_factory, policy_client_factory)):
        raise ValueError("Programme configuration needs its explicit central readers")
    if expected_execution_binding_hash is not None and (type(expected_execution_binding_hash) is not str
            or re.fullmatch('[0-9a-f]{64}', expected_execution_binding_hash) is None):
        raise ValueError('Programme execution binding requires its approved hash')
    lock, used = Lock(), []
    def distinct(factory):
        def owned():
            client = factory()
            with lock:
                if any(client is prior for prior in used):
                    raise ValueError("Configuration authority reads cannot reuse an owned connection")
                used.append(client)
            return client
        return owned
    selection_factory = distinct(selection_client_factory)
    identity = dict(configuration_id=configuration_id, expected_content_hash=expected_content_hash,
                    concurrency=concurrency)
    selected = await load_configuration_selection(selection_factory, **identity)
    registration = program_strategy_executor(selected.header["strategy_id"], selected.header["strategy_revision"])
    result = await load_program_configuration_references(selected,
        release_client_factory=distinct(release_client_factory), policy_client_factory=distinct(policy_client_factory),
        policy_dispatch=policy_dispatch, concurrency=concurrency)
    if expected_execution_binding_hash is not None:
        from dataclasses import replace
        from .configuration_execution_binding_reader import load_configuration_execution_binding
        from src.trading_runtime.configuration_execution_binding import bind_execution_configuration
        binding = await load_configuration_execution_binding(selection_factory,
            configuration_id=configuration_id, expected_configuration_hash=expected_content_hash,
            expected_content_hash=expected_execution_binding_hash)
        result = replace(result, revision=bind_execution_configuration(result.revision, binding),
            execution_binding=binding)
    current = await load_configuration_selection(selection_factory, **identity)
    if current != selected or program_strategy_executor(*registration.key) is not registration:
        raise ValueError("Configuration selection or installed pins changed during reference admission")
    return result


@dataclass(frozen=True, slots=True)
class ConfigurationReferenceReader:
    endpoint: str
    release_client_factory: object
    policy_client_factory: object
    policy_dispatch: object
    concurrency: int

    def verify(self, selected):
        """Blocking operator-worker boundary; never an arbitrary verifier callback."""
        def scoped(factory):
            def owned():
                client = factory()
                if not self.endpoint or getattr(client, "base_url", None) != self.endpoint:
                    client.close()
                    raise ValueError("Configuration reference reader changed central endpoint")
                return client
            return owned
        return asyncio.run(load_program_configuration_references(selected,
            release_client_factory=scoped(self.release_client_factory),
            policy_client_factory=scoped(self.policy_client_factory), policy_dispatch=self.policy_dispatch,
            concurrency=self.concurrency))
