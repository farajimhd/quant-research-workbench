from __future__ import annotations

import pytest

from src.backend.live_signal_completion_keeper import (
    CompletionKeeperFence, completion_resource,
)


class NodeExistsError(Exception):
    pass


class NoNodeError(Exception):
    pass


class BadVersionError(Exception):
    pass


class Stat:
    def __init__(self, version, ephemeral_owner):
        self.version = version
        self.ephemeralOwner = ephemeral_owner


class Transaction:
    def __init__(self, client):
        self.client = client
        self.ops = []

    def set_data(self, path, value, *, version):
        self.ops.append(("set", path, value, version))
        return self

    def create(self, path, value, *, ephemeral=False):
        self.ops.append(("create", path, value, ephemeral))
        return self

    def check(self, path, *, version):
        self.ops.append(("check", path, version))
        return self

    def commit(self):
        if self.client.before_commit is not None:
            hook, self.client.before_commit = self.client.before_commit, None
            hook()
        try:
            for op in self.ops:
                path = op[1]
                node = self.client.nodes.get(path)
                if op[0] == "create" and node is not None:
                    raise NodeExistsError(path)
                if op[0] in {"check", "set"}:
                    if node is None:
                        raise NoNodeError(path)
                    expected = op[2] if op[0] == "check" else op[3]
                    if node[1] != expected:
                        raise BadVersionError(path)
        except Exception as exc:
            return [exc]
        for op in self.ops:
            if op[0] == "set":
                old = self.client.nodes[op[1]]
                self.client.nodes[op[1]] = (op[2], old[1] + 1, old[2])
            elif op[0] == "create":
                self.client.nodes[op[1]] = (
                    op[2], 0, self.client.client_id[0] if op[3] else 0)
        return [None] * len(self.ops)


class FakeKazoo:
    connected = True
    client_id = (101, b"secret")

    def __init__(self):
        self.nodes = {}
        self.before_commit = None

    def ensure_path(self, path):
        return None

    def create(self, path, value):
        if path in self.nodes:
            raise NodeExistsError(path)
        self.nodes[path] = (value, 0, 0)

    def exists(self, path):
        return Stat(*self.nodes[path][1:]) if path in self.nodes else None

    def get(self, path):
        if path not in self.nodes:
            raise NoNodeError(path)
        value, version, owner = self.nodes[path]
        return value, Stat(version, owner)

    def delete(self, path, *, version):
        if path not in self.nodes:
            raise NoNodeError(path)
        if self.nodes[path][1] != version:
            raise BadVersionError(path)
        del self.nodes[path]

    def transaction(self):
        return Transaction(self)


def test_completion_keeper_persistent_proof_and_stale_epoch() -> None:
    client = FakeKazoo()
    keeper = CompletionKeeperFence(client, endpoint="127.0.0.1:9181")
    resource = completion_resource("2026-09-24", 1, 0, "plan:event")
    first = keeper.acquire_completion_claim(resource, owner_id="owner-1")
    assert first["epoch"] == 1
    assert keeper.acquire_completion_claim(resource, owner_id="owner-2") is None
    keeper.attest_completion(resource, owner_id="owner-1", epoch=1,
                             content_hash="a" * 64)
    assert keeper.release_completion_claim(resource, owner_id="owner-1", epoch=1)
    assert keeper.completion_proof_matches(resource, owner_id="owner-1", epoch=1,
                                           content_hash="a" * 64)
    second = keeper.acquire_completion_claim(resource, owner_id="owner-2")
    assert second["epoch"] == 2
    with pytest.raises(RuntimeError, match="lost"):
        keeper.attest_completion(resource, owner_id="owner-1", epoch=1,
                                 content_hash="b" * 64)


def test_completion_keeper_cas_rejects_holder_replacement_between_read_and_commit() -> None:
    client = FakeKazoo()
    keeper = CompletionKeeperFence(client, endpoint="127.0.0.1:9181")
    resource = completion_resource("2026-09-24", 1, 0, "plan:event")
    keeper.acquire_completion_claim(resource, owner_id="owner-1")
    base = keeper._base(resource)

    def replace_holder():
        value, version, owner = client.nodes[f"{base}/holder"]
        client.nodes[f"{base}/holder"] = (value, version + 1, owner)

    client.before_commit = replace_holder
    with pytest.raises(RuntimeError, match="CAS failed"):
        keeper.attest_completion(resource, owner_id="owner-1", epoch=1,
                                 content_hash="a" * 64)
    assert not keeper.completion_proof_matches(resource, owner_id="owner-1",
                                               epoch=1, content_hash="a" * 64)


def test_completion_keeper_rejects_remote_unauthenticated_endpoint() -> None:
    with pytest.raises(ValueError, match="loopback"):
        CompletionKeeperFence(FakeKazoo(), endpoint="192.168.0.21:9181")
