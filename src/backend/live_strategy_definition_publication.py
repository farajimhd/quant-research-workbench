"""Inactive typed definition publication; fake-tested, never called by live route.

Only exact CH readback precedes Keeper head CAS. An ambiguous insert is terminal:
there is no MergeTree uniqueness guarantee and this module never retries it.
"""
from __future__ import annotations

from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass
import queue
import threading
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from src.backend.live_strategy_definition_authority import (
    project_enable_change, project_installed_definition, recover_installed_definition,
)
from src.backend.live_strategy_definition_cold_reader import (
    DefinitionHead, DefinitionRowReader, KeeperDefinitionHeadReader, _hex,
    cold_read_installed_definition,
)


class DefinitionStorage(DefinitionRowReader, Protocol):
    def insert_definition(self, row: Mapping[str, Any]) -> None: ...
    def insert_enable_change(self, row: Mapping[str, Any]) -> None: ...


class KeeperDefinitionHeadFence(KeeperDefinitionHeadReader):
    """Persistent epoch + ephemeral holder; CAS head under both versions."""

    def _base(self, strategy_id: str, strategy_revision: int) -> str:
        return self.path(strategy_id, strategy_revision).removesuffix("/head")

    @staticmethod
    def _owner(owner_id: str) -> str:
        if (type(owner_id) is not str or not owner_id
                or any(char in owner_id for char in ("\r", "\n", "\x00"))):
            raise ValueError("definition Keeper owner is invalid")
        return owner_id

    def acquire(self, strategy_id: str, strategy_revision: int, *, owner_id: str) -> int | None:
        owner = self._owner(owner_id)
        self._connected()
        base = self._base(strategy_id, strategy_revision)
        self._client.ensure_path(base)
        try:
            self._client.create(f"{base}/epoch", b"0")
        except Exception as exc:
            if type(exc).__name__ != "NodeExistsError":
                raise RuntimeError("definition Keeper epoch initialization failed") from exc
        for _ in range(8):
            self._connected()
            if self._client.exists(f"{base}/holder") is not None:
                return None
            raw, stat = self._client.get(f"{base}/epoch")
            try:
                epoch = int(raw) + 1
                if epoch < 1 or str(epoch - 1).encode() != raw:
                    raise ValueError
            except ValueError as exc:
                raise ValueError("definition Keeper epoch is corrupt") from exc
            txn = self._client.transaction()
            txn.set_data(f"{base}/epoch", str(epoch).encode(), version=stat.version)
            txn.create(f"{base}/holder", f"1\n{owner}\n{epoch}".encode(), ephemeral=True)
            errors = [item for item in txn.commit() if isinstance(item, BaseException)]
            if errors:
                if all(type(item).__name__ in {
                        "BadVersionError", "NodeExistsError", "RolledBackError"} for item in errors):
                    continue
                raise RuntimeError("definition Keeper acquisition CAS failed")
            if not self.is_current(strategy_id, strategy_revision,
                                   owner_id=owner, epoch=epoch):
                raise RuntimeError("definition Keeper claim lost after acquisition")
            return epoch
        raise RuntimeError("definition Keeper acquisition contention exceeded")

    def _connected(self) -> None:
        if not getattr(self._client, "connected", False) or self._client.client_id is None:
            raise RuntimeError("definition Keeper session is unavailable")

    def is_current(self, strategy_id: str, strategy_revision: int, *,
                   owner_id: str, epoch: int) -> bool:
        self._connected()
        try:
            raw, stat = self._client.get(
                f"{self._base(strategy_id, strategy_revision)}/holder")
        except Exception as exc:
            if type(exc).__name__ == "NoNodeError":
                return False
            raise RuntimeError("definition Keeper holder read failed") from exc
        return (raw == f"1\n{self._owner(owner_id)}\n{epoch}".encode()
                and stat.ephemeralOwner == self._client.client_id[0])

    def attest(self, strategy_id: str, strategy_revision: int, *, owner_id: str,
               epoch: int, previous: DefinitionHead | None,
               definition_content_hash: str, change_sequence: int,
               change_content_hash: str) -> DefinitionHead:
        self._connected()
        base = self._base(strategy_id, strategy_revision)
        if not self.is_current(strategy_id, strategy_revision,
                               owner_id=owner_id, epoch=epoch):
            raise RuntimeError("definition Keeper owner fence was lost")
        if (change_sequence != (1 if previous is None else previous.change_sequence + 1)
                or previous is not None and
                previous.definition_content_hash != definition_content_hash):
            raise ValueError("definition Keeper head sequence or identity differs")
        _hex(definition_content_hash)
        _hex(change_content_hash)
        epoch_raw, epoch_stat = self._client.get(f"{base}/epoch")
        holder_raw, holder_stat = self._client.get(f"{base}/holder")
        if (epoch_raw != str(epoch).encode()
                or holder_raw != f"1\n{owner_id}\n{epoch}".encode()
                or holder_stat.ephemeralOwner != self._client.client_id[0]):
            raise RuntimeError("definition Keeper owner epoch changed")
        raw = (f"1\n{strategy_id}\n{strategy_revision}\n{definition_content_hash}"
               f"\n{change_sequence}\n{change_content_hash}").encode()
        txn = self._client.transaction()
        txn.check(f"{base}/epoch", version=epoch_stat.version)
        txn.check(f"{base}/holder", version=holder_stat.version)
        if previous is None:
            if self._client.exists(f"{base}/head") is not None:
                raise RuntimeError("definition Keeper head already exists")
            txn.create(f"{base}/head", raw)
        else:
            if self.read_head(strategy_id, strategy_revision) != previous:
                raise RuntimeError("definition Keeper head changed")
            txn.set_data(f"{base}/head", raw, version=previous.keeper_version)
        if any(isinstance(item, BaseException) for item in txn.commit()):
            raise RuntimeError("definition Keeper head attestation CAS failed")
        confirmed = self.read_head(strategy_id, strategy_revision)
        if (confirmed.definition_content_hash != definition_content_hash
                or confirmed.change_sequence != change_sequence
                or confirmed.change_content_hash != change_content_hash):
            raise RuntimeError("definition Keeper head readback differs")
        return confirmed

    def release(self, strategy_id: str, strategy_revision: int, *,
                owner_id: str, epoch: int) -> bool:
        if not self.is_current(strategy_id, strategy_revision,
                               owner_id=owner_id, epoch=epoch):
            return False
        path = f"{self._base(strategy_id, strategy_revision)}/holder"
        raw, stat = self._client.get(path)
        if (raw != f"1\n{owner_id}\n{epoch}".encode()
                or stat.ephemeralOwner != self._client.client_id[0]):
            return False
        try:
            self._client.delete(path, version=stat.version)
        except Exception as exc:
            if type(exc).__name__ in {"NoNodeError", "BadVersionError"}:
                return False
            raise RuntimeError("definition Keeper release failed") from exc
        return True


def publish_installed_definition(
    storage: DefinitionStorage, keeper: KeeperDefinitionHeadFence, saved: Mapping[str, Any],
    *, owner_id: str, changed_at: Any | None = None,
) -> DefinitionHead:
    """Control-plane only: insert once, exact readback, then attest next head."""
    definition = project_installed_definition(saved)
    strategy_id = definition["strategy_id"]
    revision = definition["strategy_revision"]
    epoch = keeper.acquire(strategy_id, revision, owner_id=owner_id)
    if epoch is None:
        raise RuntimeError("definition Keeper claim is held by another owner")
    try:
        existing = storage.read_definition_rows(
            strategy_id=strategy_id, strategy_revision=revision)
        if keeper._client.exists(keeper.path(strategy_id, revision)) is None:
            # Absence is permitted only when no definition/change rows exist.
            if existing or storage.read_enable_change_rows(
                    strategy_id=strategy_id, strategy_revision=revision):
                raise RuntimeError("definition has uncommitted rows without Keeper head")
            prior = None
        else:
            prior = keeper.read_head(strategy_id, revision)
        if prior is None:
            change = project_enable_change(
                definition, change_sequence=1, enabled=saved["enabled"],
                changed_at=definition["created_at"])
            storage.insert_definition(definition)
        else:
            if (len(existing) != 1 or existing[0] != definition
                    or prior.definition_content_hash != definition["content_hash"]):
                raise ValueError("definition differs from prior Keeper authority")
            cold_read_installed_definition(
                storage, keeper, strategy_id=strategy_id, strategy_revision=revision)
            change = project_enable_change(
                definition, change_sequence=prior.change_sequence + 1,
                enabled=saved["enabled"], changed_at=changed_at,
                previous_change_hash=prior.change_content_hash)
        storage.insert_enable_change(change)
        definitions = storage.read_definition_rows(
            strategy_id=strategy_id, strategy_revision=revision)
        changes = storage.read_enable_change_rows(
            strategy_id=strategy_id, strategy_revision=revision)
        recovered = recover_installed_definition(
            definitions, changes, expected_change_sequence=change["change_sequence"],
            expected_change_hash=change["content_hash"])
        if recovered["enabled"] != saved["enabled"] or definitions != [definition]:
            raise ValueError("definition publication readback differs")
        return keeper.attest(
            strategy_id, revision, owner_id=owner_id, epoch=epoch,
            previous=prior, definition_content_hash=definition["content_hash"],
            change_sequence=change["change_sequence"],
            change_content_hash=change["content_hash"])
    finally:
        keeper.release(strategy_id, revision, owner_id=owner_id, epoch=epoch)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise ValueError("definition packet contains unmodeled value")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True, init=False)
class DefinitionPublicationPacket:
    saved: Mapping[str, Any]
    changed_at: str | None
    strategy_id: str
    strategy_revision: int

    @classmethod
    def _prepared(cls, saved: Mapping[str, Any], changed_at: str | None,
                  strategy_id: str, strategy_revision: int) -> "DefinitionPublicationPacket":
        packet = object.__new__(cls)
        for key, value in (("saved", _freeze(saved)), ("changed_at", changed_at),
                           ("strategy_id", strategy_id),
                           ("strategy_revision", strategy_revision)):
            object.__setattr__(packet, key, value)
        return packet


def prepare_definition_publication(
    saved: Mapping[str, Any], *, changed_at: str | None = None,
) -> DefinitionPublicationPacket:
    """Control-plane preparation; immutable packet makes queue submit O(1)."""
    definition = project_installed_definition(saved)
    if changed_at is not None:
        # Validate causal clock before enqueue; first publication still requires
        # its immutable creation time and is checked again by the worker.
        from src.backend.live_strategy_definition_authority import _time
        changed_at = _time(changed_at)
    return DefinitionPublicationPacket._prepared(
        saved, changed_at, definition["strategy_id"], definition["strategy_revision"])


class _Receipt(Future[DefinitionHead]):
    def cancel(self) -> bool:
        return False


class DefinitionPublicationQueue:
    """Lazy bounded control-plane worker; any uncertain failure halts the lane."""

    def __init__(self, storage: DefinitionStorage, keeper: KeeperDefinitionHeadFence,
                 *, owner_id: str, capacity: int = 128) -> None:
        if type(capacity) is not int or capacity < 1:
            raise ValueError("definition publication capacity is invalid")
        self._storage = storage
        self._keeper = keeper
        self._owner_id = KeeperDefinitionHeadFence._owner(owner_id)
        self._capacity = capacity
        self._queue: queue.Queue[tuple[DefinitionPublicationPacket, Future[DefinitionHead]]] = queue.Queue()
        self._pending: set[tuple[str, int]] = set()
        self._lock = threading.Lock()
        self._fatal: BaseException | None = None
        self._closing = False
        self._thread = threading.Thread(target=self._work, name="typed-definition-publication",
                                        daemon=False)
        self._started = False

    def submit(self, packet: DefinitionPublicationPacket) -> Future[DefinitionHead]:
        """O(1) ownership handoff; no Keeper or ClickHouse I/O here."""
        if not isinstance(packet, DefinitionPublicationPacket):
            raise TypeError("prepared immutable definition packet is required")
        key = (packet.strategy_id, packet.strategy_revision)
        with self._lock:
            if self._closing or self._fatal is not None:
                raise RuntimeError("definition publication lane is unavailable")
            if key in self._pending:
                raise ValueError("definition publication is already pending")
            if len(self._pending) >= self._capacity:
                raise RuntimeError("definition publication capacity is exhausted")
            receipt: Future[DefinitionHead] = _Receipt()
            self._pending.add(key)
            self._queue.put_nowait((packet, receipt))
            if not self._started:
                self._thread.start()
                self._started = True
            return receipt

    def close(self, *, timeout: float = 10.0) -> None:
        with self._lock:
            self._closing = True
        if self._started:
            self._thread.join(timeout=timeout)
        if self._started and self._thread.is_alive():
            raise RuntimeError("definition publication worker did not drain")

    def _work(self) -> None:
        while True:
            try:
                packet, receipt = self._queue.get(timeout=0.1)
            except queue.Empty:
                with self._lock:
                    if self._closing:
                        return
                continue
            try:
                with self._lock:
                    fatal = self._fatal
                if fatal is not None:
                    raise RuntimeError("definition publication halted") from fatal
                head = publish_installed_definition(
                    self._storage, self._keeper, _thaw(packet.saved),
                    owner_id=self._owner_id, changed_at=packet.changed_at)
            except BaseException as exc:
                with self._lock:
                    if self._fatal is None:
                        self._fatal = exc
                    self._closing = True
                    self._pending.remove((packet.strategy_id, packet.strategy_revision))
                try:
                    receipt.set_exception(exc)
                except InvalidStateError:
                    pass
            else:
                with self._lock:
                    self._pending.remove((packet.strategy_id, packet.strategy_revision))
                try:
                    receipt.set_result(head)
                except InvalidStateError:
                    pass
            finally:
                self._queue.task_done()
