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
