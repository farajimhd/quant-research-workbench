"""No ClickHouse or Keeper connection: terminal claim guarding only."""
from contextlib import asynccontextmanager
import asyncio

import pytest

from src.backend.backtest_terminal_v2_keeper import FixedTerminalKeeperAuthority
from src.trading_runtime import keeper_ownership as ownership
from threading import Lock


RUN = "00000000-0000-0000-0000-000000000b01"


class FakeKeeper:
    def __init__(self):
        self.current = True
        self.claimed = []
        self.renewals = 0

    @asynccontextmanager
    async def claim_portfolio_snapshot(self, run_id, account_id):
        lease = {"resource_id": f"{run_id}:{account_id}",
                 "owner_id": account_id, "epoch": 1, "expires_at": "later"}
        self.claimed.append(account_id)
        try:
            yield lease
        finally:
            self.claimed.remove(account_id)

    def portfolio_snapshot_claim_is_current(self, lease):
        return self.current and lease["owner_id"] in self.claimed

    def renew_portfolio_admission_lease(self, resource_id, *, owner_id, epoch):
        self.renewals += 1
        return {"resource_id": resource_id, "owner_id": owner_id,
                "epoch": epoch, "expires_at": "later"} if self.current else None


class FakeClient:
    def __init__(self):
        self.calls = []

    def execute(self, sql):
        self.calls.append(sql)
        return ""


def test_claims_all_accounts_renews_before_and_after_fake_write():
    async def run():
        keeper, client = FakeKeeper(), FakeClient()
        authority = FixedTerminalKeeperAuthority(
            keeper=keeper, client=client, run_id=RUN, account_ids=("DU2", "DU1"))
        with pytest.raises(RuntimeError, match="not held"):
            authority.client.execute("INSERT fake")
        async with authority:
            authority.client.execute("INSERT fake")
            assert client.calls == ["INSERT fake"]
            assert keeper.renewals >= 4
            keeper.current = False
            with pytest.raises(RuntimeError, match="expired or changed"):
                authority.client.execute("INSERT second")
            assert client.calls == ["INSERT fake"]
        assert keeper.claimed == []
    asyncio.run(run())


def test_claim_failure_releases_earlier_account():
    class Contended(FakeKeeper):
        @asynccontextmanager
        async def claim_portfolio_snapshot(self, run_id, account_id):
            if account_id == "DU2":
                raise RuntimeError("contended")
            async with super().claim_portfolio_snapshot(run_id, account_id) as lease:
                yield lease
    async def run():
        keeper = Contended()
        authority = FixedTerminalKeeperAuthority(
            keeper=keeper, client=FakeClient(), run_id=RUN,
            account_ids=("DU1", "DU2"))
        with pytest.raises(RuntimeError, match="contended"):
            async with authority:
                pass
        assert keeper.claimed == []
    asyncio.run(run())


def test_lease_loss_during_fake_write_fails_post_write_check():
    async def run():
        keeper = FakeKeeper()
        class LosingClient(FakeClient):
            def execute(self, sql):
                result = super().execute(sql)
                keeper.current = False
                return result
        client = LosingClient()
        authority = FixedTerminalKeeperAuthority(
            keeper=keeper, client=client, run_id=RUN, account_ids=("DU1",))
        async with authority:
            with pytest.raises(RuntimeError, match="expired or changed"):
                authority.client.execute("INSERT fake")
            assert client.calls == ["INSERT fake"]
    asyncio.run(run())


def test_persistent_keeper_cas_binds_exact_seal_and_account_hashes():
    class NoNodeError(Exception):
        pass
    class NodeExistsError(Exception):
        pass
    class Stat:
        version = 1
        ephemeralOwner = 44
    class Txn:
        def __init__(self, client):
            self.client = client
            self.created = None
            self.checked = []
        def check(self, path, *, version):
            self.checked.append((path, version))
        def create(self, path, value, *, ephemeral):
            assert not ephemeral
            self.created = (path, value)
        def commit(self):
            assert len(self.checked) == 2
            if self.created[0] in self.client.rows:
                return [NodeExistsError()]
            self.client.rows[self.created[0]] = self.created[1]
            return []
    class Client:
        connected = True
        client_state = "CONNECTED"
        client_id = (44, "")
        def __init__(self):
            self.rows = {}
        def get(self, path):
            if path not in self.rows:
                raise NoNodeError()
            return self.rows[path], Stat()
        def transaction(self):
            return Txn(self)
    client = Client()
    keeper = object.__new__(ownership.KeeperOwnershipCoordinator)
    keeper._client = client
    keeper._lock = Lock()
    keeper.portfolio_snapshot_claim_is_current = lambda _lease: True
    resource = ownership._sync_resource_id(RUN, "DU1")
    base = ownership._path("portfolio", resource)
    client.rows[f"{base}/holder"] = ownership._encode("owner", 1, "portfolio")
    client.rows[f"{base}/epoch"] = b"1"
    lease = {"resource_id": resource, "owner_id": "owner", "epoch": 1}
    batch = "00000000-0000-0000-0000-000000000b04"
    proof = keeper.attest_backtest_terminal_v2(
        (("DU1", lease),), run_id=RUN, batch_id=batch,
        seal_hash="a" * 64, accounts_hash="b" * 64)
    assert keeper.load_backtest_terminal_v2_attestation(RUN, batch) == proof
    assert proof.decode().split("\n")[3:5] == ["a" * 64, "b" * 64]
    with pytest.raises(ownership.KeeperUnavailable, match="conflicts"):
        keeper.attest_backtest_terminal_v2(
            (("DU1", lease),), run_id=RUN, batch_id=batch,
            seal_hash="c" * 64, accounts_hash="b" * 64)


def test_cold_reader_rejects_missing_or_tampered_keeper_proof(monkeypatch):
    from hashlib import sha256
    from src.backend import backtest_terminal_v2_keeper as terminal
    from src.backend import backtest_terminal_v2_accounts as account_reader
    from src.backend import backtest_terminal_v2_publication as publication
    from src.trading_runtime import arte_journal_writer
    from src.trading_runtime.journal_contract import canonical_json

    batch = "00000000-0000-0000-0000-000000000b04"
    seal = {"run_id": RUN, "batch_id": batch, "last_sequence": 4}
    accounts = {"DU1": {"state_hash": "a" * 64}}
    monkeypatch.setattr(arte_journal_writer, "load_typed_run_context",
                        lambda *_: {"account_ids": ("DU1",)})
    monkeypatch.setattr(publication, "audit_terminal_v2_run", lambda *_, **__: seal)
    monkeypatch.setattr(account_reader, "load_terminal_v2_portfolio_accounts",
                        lambda *_, **__: accounts)
    class Keeper:
        proof = None
        def load_backtest_terminal_v2_attestation(self, *_):
            return self.proof
    keeper = Keeper()
    with pytest.raises(RuntimeError, match="lacks committed Keeper proof"):
        terminal.load_attested_terminal_v2_accounts(object(), keeper, run_id=RUN)
    seal_hash = sha256(canonical_json(seal).encode()).hexdigest()
    account_hash = sha256(canonical_json([("DU1", "a" * 64)]).encode()).hexdigest()
    keeper.proof = (f"2\n{RUN}\n{batch}\n{seal_hash}\n{account_hash}"
                    "\n1\nDU1\nowner\n1").encode()
    assert terminal.load_attested_terminal_v2_accounts(
        object(), keeper, run_id=RUN) == accounts
    assert terminal.load_attested_terminal_v2_state(
        object(), keeper, run_id=RUN) == (seal, accounts)
    valid_proof = keeper.proof
    for malformed in (
        valid_proof.rsplit(b"\n", 2)[0],
        valid_proof + b"\nextra",
        valid_proof.rsplit(b"\n", 1)[0] + b"\n0",
        valid_proof.replace(b"\nowner\n", b"\n\n"),
    ):
        keeper.proof = malformed
        with pytest.raises(RuntimeError, match="proof differs"):
            terminal.load_attested_terminal_v2_accounts(object(), keeper, run_id=RUN)
    keeper.proof = valid_proof
    keeper.proof = keeper.proof.replace(seal_hash.encode(), ("f" * 64).encode())
    with pytest.raises(RuntimeError, match="proof differs"):
        terminal.load_attested_terminal_v2_accounts(object(), keeper, run_id=RUN)
