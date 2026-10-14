import asyncio
from dataclasses import replace
from threading import Event

import pytest

from src.backend import certified_program_configuration as module
from src.trading_runtime import strategy_registry as registry, strategy_configuration_schema as schema
from src.trading_runtime.arte_portfolio_policy import _policy_rows
from src.trading_runtime.portfolio import PortfolioPolicy
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
from tests.test_program_strategy_registry import installed
from tests.test_columnar_strategy_release import Reader
from tests.test_strategy_configuration_selection import children, prepare, ID


@pytest.mark.parametrize('foreign_parent', [False, True])
def test_certified_selection_joins_approved_execution_binding_and_rechecks_selection(monkeypatch, foreign_parent):
    from types import SimpleNamespace
    from datetime import datetime, UTC
    from tests.test_strategy_configuration_materialization import configured
    from src.backend.strategy_configuration_materialization import materialize_program_configuration
    from src.backend import configuration_execution_binding_reader as binding_reader
    from src.trading_runtime.configuration_execution_binding import project_execution_binding
    from src.trading_runtime.execution_policies import ExecutionPolicy
    _, actor, selected, policies = configured()
    revision = materialize_program_configuration(selected, actor.program_release, policies)
    binding = project_execution_binding(configuration_id=selected.header['configuration_id'],
        configuration_hash=('b' * 64 if foreign_parent else selected.header['content_hash']),
        policies=(ExecutionPolicy(),), entry_policy_id='adaptive-regular', exit_policy_id='adaptive-regular',
        tick_size=.01, limit_offset_bps=2., published_at=datetime.now(UTC))
    registration = SimpleNamespace(key=(actor.strategy_id, actor.revision))
    monkeypatch.setattr(module, 'program_strategy_executor', lambda *_: registration)
    reads = []
    async def selection(*args, **kwargs):
        reads.append('selection'); return selected
    async def references(*args, **kwargs):
        return module.CertifiedProgramConfiguration(selected, actor.program_release, revision)
    async def execution(factory, **kwargs):
        assert kwargs['expected_content_hash'] == binding.header['content_hash']
        reads.append('binding'); return binding
    monkeypatch.setattr(module, 'load_configuration_selection', selection)
    monkeypatch.setattr(module, 'load_program_configuration_references', references)
    monkeypatch.setattr(binding_reader, 'load_configuration_execution_binding', execution)
    async def run():
        return await module.load_certified_program_configuration(selection_client_factory=lambda: None,
            release_client_factory=lambda: None, policy_client_factory=lambda: None, policy_dispatch=object(),
            configuration_id=selected.header['configuration_id'], expected_content_hash=selected.header['content_hash'],
            concurrency=1, expected_execution_binding_hash=binding.header['content_hash'])
    if foreign_parent:
        with pytest.raises(ValueError, match='selected configuration'):
            asyncio.run(run())
    else:
        result = asyncio.run(run())
        assert result.execution_binding is binding
        assert result.revision['execution_binding_hash'] == binding.header['content_hash']
        assert reads == ['selection', 'binding', 'selection']


@pytest.mark.parametrize("failure", [None, "pin", "policy", "changed", "cancel"])
def test_configuration_reference_admission_uses_installed_release_and_settles_owned_reads(monkeypatch, failure):
    registration, release_rows, _ = installed(monkeypatch)
    release_id = next(iter(release_rows["trading_columnar_strategy_release_v1"]))["release_id"]
    policy = PortfolioPolicy(policy_id="central")
    digest = _policy_rows(policy)[0]
    rows = children()
    rows[schema.ACCOUNT.name][0]["policy_hash"] = digest
    packet = prepare(rows, strategy_id=registration.strategy_id, strategy_revision=registration.revision,
        program_release_id=release_id, program_release_hash=("c" * 64 if failure == "pin" else registration.expected_content_hash))
    selection_reads = []
    async def selection(*args, **kwargs):
        selection_reads.append(kwargs)
        return packet
    monkeypatch.setattr(module, "load_configuration_selection", selection)
    monkeypatch.setattr(module, "storage_preflight", lambda *_args, **_kwargs: None)
    entered, release = Event(), Event()
    if failure != "cancel": release.set()
    clients, releases = [], []
    class PolicyClient:
        closed = False
        def execute(self, query):
            assert query == "SELECT getSetting('readonly')"
            return "1"
        def close(self): self.closed = True
    def policy_factory():
        client = PolicyClient()
        clients.append(client)
        return client
    def release_factory():
        client = Reader(release_rows)
        releases.append(client)
        return client
    dispatch = TypedInsertDispatch(object())
    def attested(client, actual_dispatch, actual_digest):
        assert actual_dispatch is dispatch and actual_digest == digest
        entered.set()
        assert release.wait(5)
        if failure == "changed":
            registry._PROGRAM_REGISTRY[registration.key] = replace(registration)
        return replace(policy, revision=2) if failure == "policy" else policy
    monkeypatch.setattr(module, "load_attested_portfolio_policy", attested)
    async def run():
        task = asyncio.create_task(module.load_certified_program_configuration(
            selection_client_factory=lambda: pytest.fail("selection transport separately tested"),
            release_client_factory=release_factory, policy_client_factory=policy_factory,
            policy_dispatch=dispatch, configuration_id=ID,
            expected_content_hash=packet.header["content_hash"], concurrency=2))
        if failure == "cancel":
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task, 5)
        elif failure:
            with pytest.raises(ValueError): await task
        else:
            result = await task
            assert result.revision["payload"]["portfolio"]["policies"][0]["policy_id"] == "central"
            assert result.release.header["content_hash"] == registration.expected_content_hash
            assert len(selection_reads) == 2
    asyncio.run(run())
    assert all(client.closed for client in clients + releases)
    if failure == "pin": assert not clients and not releases


def test_policy_read_adapter_preserves_existing_catalogue_codec_and_bounds(monkeypatch):
    from tests.test_arte_portfolio_policy import Catalog
    from src.trading_runtime.arte_portfolio_policy import publish_portfolio_policy, load_portfolio_policy
    policy = PortfolioPolicy(policy_id="bounded")
    source = Catalog()
    digest = publish_portfolio_policy(source, policy)
    queries = []
    class Client:
        def execute(self, query):
            queries.append(query)
            return source.execute(query)
    bounded = module._BoundedPolicyReader(Client())
    assert load_portfolio_policy(bounded, digest) == policy
    assert all(" LIMIT " in query for query in queries)
    with pytest.raises(ValueError, match="defined catalogue SELECT"):
        bounded.execute("SELECT * FROM arte.other FORMAT JSONEachRow")
    class Duplicate:
        def execute(self, query): return '{"policy_hash":"' + digest + '","policy_hash":"bad"}'
    with pytest.raises(ValueError, match="duplicate fields"):
        module._BoundedPolicyReader(Duplicate()).execute(queries[0].replace("LIMIT 2 ", ""))


def test_builtin_reference_reader_verifies_real_release_and_policy_receipt_codecs(monkeypatch):
    from tests.test_arte_portfolio_policy import Catalog
    from tests.test_arte_typed_insert_dispatch import Keeper
    from src.trading_runtime.arte_portfolio_policy import publish_portfolio_policy
    registration, release_rows, _ = installed(monkeypatch)
    dispatch = TypedInsertDispatch(Keeper())
    class StrictCatalog(Catalog):
        typed_insert_strict = True
        typed_insert_dispatch = dispatch
        def execute(self, sql, *, query_id=None): return super().execute(sql)
    catalogue = StrictCatalog()
    policy = PortfolioPolicy(policy_id="attested-central")
    digest = publish_portfolio_policy(catalogue, policy)
    rows = children()
    rows[schema.ACCOUNT.name][0]["policy_hash"] = digest
    release_id = release_rows["trading_columnar_strategy_release_v1"][0]["release_id"]
    packet = prepare(rows, strategy_id=registration.strategy_id, strategy_revision=registration.revision,
        program_release_id=release_id, program_release_hash=registration.expected_content_hash)
    clients = []
    def releases():
        client = Reader(release_rows)
        client.base_url = "http://central"
        clients.append(client)
        return client
    class PolicyReader:
        base_url = "http://central"
        closed = False
        def close(self): self.closed = True
        def execute(self, sql):
            if sql == "SELECT getSetting('readonly')": return "1"
            return catalogue.execute(sql)
    def policies():
        client = PolicyReader()
        clients.append(client)
        return client
    # Policy metadata placement is tested separately; receipt/content codecs are real here.
    monkeypatch.setattr(module, "storage_preflight", lambda *_args, **_kwargs: None)
    reader = module.ConfigurationReferenceReader("http://central", releases, policies, dispatch, 2)
    result = reader.verify(packet)
    assert result.selection == packet
    assert result.revision["payload"]["portfolio"]["policies"][0]["policy_id"] == policy.policy_id
    assert all(client.closed for client in clients)
    with pytest.raises(ExceptionGroup) as caught:
        replace(reader, endpoint="http://wrong").verify(packet)
    assert len(caught.value.exceptions) == 2
    assert all(isinstance(error, ValueError) and "endpoint" in str(error)
               for error in caught.value.exceptions)
    assert all(client.closed for client in clients)
