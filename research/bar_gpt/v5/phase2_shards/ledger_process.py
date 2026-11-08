"""Single CPU-only ledger owner, isolated from extraction-thread GIL contention.

The coordinator sends small receipt dictionaries through one bounded synchronous
pipe. No bar arrays, GPU tensors, secrets or model state cross this interface.
Acknowledgments are sent only after FULL-sync SQLite transactions commit. A
failure stops generation; immutable unregistered receipts remain recoverable.
"""

import multiprocessing as mp

from .storage import Catalog


def serve(root, connection):
    """Own one catalog until the parent closes its pipe or explicitly drains it."""
    catalog = None
    try:
        catalog = Catalog(root)
        connection.send((True, None, catalog.stats()))
        while True:
            name, args = connection.recv()
            if name == "close":
                break
            if name not in {
                "recover",
                "register_many",
                "lane_status",
                "remember_check",
                "export",
            }:
                raise ValueError("Unknown ledger operation")
            try:
                result = getattr(catalog, name)(*args)
                connection.send((True, result, catalog.stats()))
            except Exception as error:
                connection.send((False, f"{type(error).__name__}: {error}", None))
                # The coordinator drains publication on a failed transaction.
                # Leave this owner alive only to accept its explicit close.
    except (EOFError, BrokenPipeError):
        pass  # Parent exit releases its producer lock; SQLite rolls back safely.
    except Exception as error:
        try:
            connection.send((False, f"{type(error).__name__}: {error}", None))
        except (EOFError, BrokenPipeError):
            pass
    finally:
        if catalog is not None:
            catalog.db.close()
        connection.close()


class LedgerProcess:
    """Synchronous commit acknowledgment with cached, constant-size progress.

    Exactly one request can be outstanding, bounding the IPC queue to one batch
    of at most `workers` receipts. Readers/audits continue using Catalog directly.
    The spawned process imports NumPy/SQLite only and allocates no GPU memory.
    """

    def __init__(self, root):
        context = mp.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(
            target=serve, args=(str(root), child), name="phase2-ledger", daemon=True
        )
        self.process.start()
        child.close()
        self.cached = None
        try:
            self.receive()
        except BaseException:
            self.close()
            raise

    def receive(self):
        """Bound a stalled ledger wait; never accept an uncommitted acknowledgment."""
        if not self.connection.poll(90):
            raise TimeoutError("Phase-two ledger did not acknowledge within 90 seconds")
        ok, result, stats = self.connection.recv()
        if not ok:
            raise RuntimeError(f"Phase-two ledger failed: {result}")
        self.cached = stats
        return result

    def call(self, name, *args):
        """Send one small request, then release the parent's GIL while waiting."""
        self.connection.send((name, args))
        return self.receive()

    def stats(self):
        """Return the last acknowledged counters without IPC or corpus scans."""
        return self.cached

    def recover(self):
        """Reconcile only next orphan receipts under the original recovery rules."""
        return self.call("recover")

    def lane_status(self, key):
        """Fetch a stream's proof, skip status and committed cursor in one request."""
        return self.call("lane_status", key)

    def register_many(self, receipts):
        """Do not advance a lane until its entire bounded batch is committed."""
        if receipts:
            return self.call("register_many", receipts)

    def remember_check(self, proof, skipped=False):
        """Persist the unchanged, sealed source-bound proof contract."""
        return self.call("remember_check", proof, skipped)

    def export(self, plan_hash):
        """Seal the existing immutable selected-block release on graceful drain."""
        return self.call("export", plan_hash)

    def close(self):
        """Drain the owned ledger process; never touch a trainer or another PID."""
        if self.process.is_alive():
            try:
                self.connection.send(("close", ()))
            except (EOFError, BrokenPipeError, OSError):
                pass
            self.process.join(timeout=10)
            if self.process.is_alive():
                # A stuck transaction rolls back; its published receipts survive.
                self.process.terminate()
                self.process.join(timeout=5)
        self.connection.close()
