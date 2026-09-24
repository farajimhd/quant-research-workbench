from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from src.trading_runtime.arte_activation_projection import ActivationProjection
from src.trading_runtime.arte_activation_writer import (
    ActivationQueueFull, ArteActivationWriter,
)


def _projection(ticker: str = "SUGP") -> ActivationProjection:
    return ActivationProjection((
        ("delivery_id", f"plan-1:{ticker}"), ("run_plan_id", "plan-1"),
        ("profile_id", "profile-1"), ("book_id", "default"),
        ("ticker", ticker), ("signal_stream_id", "squeeze"),
        ("event_id", f"event-{ticker}"),
        ("event_time", "2026-08-21T08:10:01+00:00"),
    ), (), ())


def _lease(ticker: str = "SUGP") -> dict:
    return {"resource_id": f"activation:2026-08-21:plan-1:{ticker}",
            "owner_id": "worker-1", "epoch": 1}


class _Keeper:
    def __init__(self) -> None:
        self.current = True
        self.renewals = 0
        self.releases = 0
        self.acquisitions = 0

    def acquire_portfolio_admission_lease(self, resource, *, owner_id, ttl_seconds):
        self.acquisitions += 1
        return {"resource_id": resource, "owner_id": owner_id, "epoch": 1}

    def portfolio_admission_lease_is_current(self, resource, *, owner_id, epoch):
        return self.current

    def renew_portfolio_admission_lease(self, resource, *, owner_id, epoch, ttl_seconds):
        self.renewals += 1
        return _lease(resource.rsplit(":", 1)[-1]) if self.current else None

    def release_portfolio_admission_lease(self, resource, *, owner_id, epoch):
        self.releases += 1
        return self.current


class ArteActivationWriterTests(unittest.TestCase):
    def test_submit_is_nonblocking_and_claim_releases_after_receipt(self) -> None:
        keeper = _Keeper()
        entered, release = threading.Event(), threading.Event()

        def publish(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(2))
            return "digest"

        with patch("src.trading_runtime.arte_activation_writer.publish_activation", side_effect=publish):
            writer = ArteActivationWriter(object(), keeper, capacity=1,
                                          preflight=lambda _: None,
                                          ttl_seconds=1, renewal_interval_seconds=0.02)
            try:
                start = time.monotonic()
                receipt = writer.submit(_projection(), owner_id="worker-1")
                self.assertLess(time.monotonic() - start, 0.1)
                self.assertFalse(receipt.cancel())
                self.assertTrue(entered.wait(2))
                self.assertFalse(receipt.done())
                with self.assertRaisesRegex(ValueError, "already queued"):
                    writer.submit(_projection(), owner_id="worker-1")
                with self.assertRaises(ActivationQueueFull):
                    writer.submit(_projection("AAPL"), owner_id="worker-1")
                time.sleep(0.06)
                self.assertGreater(keeper.renewals, 0)
                self.assertEqual(keeper.releases, 0)
                release.set()
                self.assertEqual(receipt.result(timeout=2), "digest")
                self.assertEqual(keeper.releases, 1)
            finally:
                release.set()
                writer.close(timeout_seconds=2)

    def test_failed_publication_retains_claim_and_stops_admission(self) -> None:
        keeper = _Keeper()
        with patch("src.trading_runtime.arte_activation_writer.publish_activation",
                   side_effect=RuntimeError("uncertain commit")):
            writer = ArteActivationWriter(object(), keeper, preflight=lambda _: None)
            receipt = writer.submit(_projection(), owner_id="worker-1")
            with self.assertRaisesRegex(RuntimeError, "uncertain commit"):
                receipt.result(timeout=2)
            self.assertEqual(keeper.releases, 0)
            with self.assertRaisesRegex(RuntimeError, "reconciliation"):
                writer.submit(_projection("AAPL"), owner_id="worker-1")
            with self.assertRaisesRegex(RuntimeError, "reconciliation"):
                writer.close(timeout_seconds=2)

    def test_submit_does_no_keeper_io(self) -> None:
        keeper = _Keeper()
        entered, release = threading.Event(), threading.Event()

        def acquire(resource, *, owner_id, ttl_seconds):
            entered.set()
            self.assertTrue(release.wait(2))
            return {"resource_id": resource, "owner_id": owner_id, "epoch": 1}

        keeper.acquire_portfolio_admission_lease = acquire
        writer = ArteActivationWriter(object(), keeper, preflight=lambda _: None)
        with patch("src.trading_runtime.arte_activation_writer.publish_activation", return_value="digest"):
            try:
                start = time.monotonic()
                receipt = writer.submit(_projection(), owner_id="worker-1")
                self.assertLess(time.monotonic() - start, 0.1)
                self.assertTrue(entered.wait(2))
                self.assertFalse(receipt.done())
                release.set()
                self.assertEqual(receipt.result(timeout=2), "digest")
            finally:
                release.set()
                writer.close(timeout_seconds=2)

    def test_invalid_ttl_rejected_before_preflight(self) -> None:
        checked = []
        with self.assertRaisesRegex(ValueError, "renewal interval or TTL"):
            ArteActivationWriter(object(), _Keeper(), ttl_seconds=1,
                                 renewal_interval_seconds=1,
                                 preflight=lambda _: checked.append(True))
        self.assertFalse(checked)


if __name__ == "__main__":
    unittest.main()
