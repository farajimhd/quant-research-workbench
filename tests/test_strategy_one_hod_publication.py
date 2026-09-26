"""HOD producer skips sealed units and never covers uncertain child writes."""

import pytest

from pipelines.strategy_one import hod_publication as subject
from src.trading_runtime.strategy_one_hod_product import HodContext


SCOPE = subject.HodPublicationScope(
    "build", "2026-08-18", "TEST",
    "00000000-0000-0000-0000-000000000001",
    "00000000-0000-0000-0000-000000000002",
    "a" * 64, "b" * 64, (300_100,))


def test_verified_unit_skips_full_100ms_derivation(monkeypatch):
    monkeypatch.setattr(subject, "_scope", lambda *_args, **_kwargs: SCOPE)
    monkeypatch.setattr(subject, "_verify_existing",
                        lambda *_args: "00000000-0000-0000-0000-000000000003")
    monkeypatch.setattr(subject, "_derive", lambda *_args:
                        pytest.fail("sealed HOD unit was recalculated"))
    assert subject.publish_unit(None, None, None, None, None, None,
                                ticker="TEST") == "skipped"


def test_uncertain_child_readback_never_publishes_coverage(monkeypatch):
    monkeypatch.setattr(subject, "_scope", lambda *_args, **_kwargs: SCOPE)
    monkeypatch.setattr(subject, "_verify_existing", lambda *_args: None)
    expected = (HodContext(300_100, 100_000, 120_000, True, "r11"),)
    monkeypatch.setattr(subject, "_derive", lambda *_args: expected)
    monkeypatch.setattr(subject, "_insert_children", lambda *_args: None)
    monkeypatch.setattr(subject, "_child", lambda *_args:
                        (HodContext(300_100, 100_000, 120_000, False, ""),))

    class Writer:
        def execute(self, _sql):
            pytest.fail("uncertain child attempted to publish coverage")

    with pytest.raises(subject.HodReadbackMismatch, match="read-back"):
        subject.publish_unit(Writer(), None, None, None, None, None,
                             ticker="TEST")
