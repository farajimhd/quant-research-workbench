"""Operator-staged immutable selection publication, never implicit app writes."""
from concurrent.futures import ThreadPoolExecutor

from .qmd_columnar_history_transport import _owned_worker
from src.trading_runtime import strategy_configuration_schema as schema
from src.trading_runtime.strategy_configuration_selection import restore_configuration_selection


class ConfigurationSelectionPublisher:
    """One fail-stop lane; children use bounded independent storage operations.

    The storage adapter owns preflight, exclusive ownership and certification
    of programme/policy references. Each insert_child call must own its own
    connection. All children settle before any selection seal is written.
    """
    def __init__(self, storage, *, concurrency):
        if type(concurrency) is not int or not 1 <= concurrency <= 6:
            raise ValueError("Configuration publication requires explicit bounded concurrency")
        self.storage = storage
        self.concurrency = concurrency
        self.busy = False
        self.failed = False

    async def publish(self, source):
        selected = restore_configuration_selection(source)
        if self.busy or self.failed:
            raise RuntimeError("Configuration publication lane is busy or stopped")
        self.busy = True
        try:
            return await _owned_worker(self._publish, selected)
        except BaseException:
            # Cancellation or ambiguous INSERT acknowledgements prohibit retries.
            self.failed = True
            raise
        finally:
            self.busy = False

    def _publish(self, selected):
        storage = self.storage
        identity = selected.header["configuration_id"]
        storage.preflight()
        storage.assert_owner()
        storage.verify_references(selected)
        existing = storage.read_selection(identity)
        if existing is not None:
            restored = restore_configuration_selection(existing)
            if restored.header["content_hash"] != selected.header["content_hash"]:
                raise ValueError("Configuration identity already has a different immutable selection")
            storage.assert_owner()
            return restored
        prior = storage.read_children(identity)
        if set(prior) != {table.name for table in schema.CHILDREN}:
            raise ValueError("Configuration publication lacks complete child readback")
        if any(prior.values()):
            raise ValueError("Unsealed configuration children require explicit recovery")
        def insert(table):
            storage.assert_owner()
            storage.insert_child(table.name, selected.rows[table.name])
            storage.assert_owner()
        errors = []
        with ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="configuration-publish") as pool:
            futures = [pool.submit(insert, table) for table in schema.CHILDREN]
            for future in futures:
                try:
                    future.result()
                except BaseException as exc:
                    errors.append(exc)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Configuration child publication failed", errors)
        actual = storage.read_children(identity)
        expected = {table.name: selected.rows[table.name] for table in schema.CHILDREN}
        if actual != expected:
            raise ValueError("Configuration child readback differs; seal withheld")
        storage.verify_references(selected)
        storage.assert_owner()
        storage.insert_seal(selected.rows[schema.SELECTION.name])
        restored = restore_configuration_selection(storage.read_selection(identity))
        if restored.header["content_hash"] != selected.header["content_hash"]:
            raise ValueError("Published configuration selection differs")
        storage.assert_owner()
        return restored
