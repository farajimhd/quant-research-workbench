"""The optional producer hook stays inert in the CLI/default builder path."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_market_day as builder

from src.trading_runtime.arte_market_day_keeper import BuildAttestation
from test_arte_market_day_source_plan import plan as source_plan_fixture


DAY = "2026-08-18"
PIN = "b" * 64


def fixture():
    plan = source_plan_fixture()
    definition = dict(version="market-day-core-v5", plan=plan,
                      calculation_source=PIN, rules_hash=PIN)

    class Ledger:
        def unit(self, build, day, ticker, stage):
            return dict(status="complete", attempt_id="attempt", source_hash="source",
                        output_rows=0, output_hash="0")

        def seed(self, build, day, ticker):
            return dict(attempt_id="attempt", mode=0, predecessor_date="",
                        prior_build_id="", prior_state_hash="")

    return definition, builder.digest(definition), Ledger()


def test_injected_hook_receives_only_prepared_certificate_and_requires_proof() -> None:
    definition, build_id, ledger = fixture()
    called = []

    def publisher(prepared, *, sessions):
        called.append((prepared, sessions))
        fence = prepared["market_day_build_fence_v1"][0]
        return BuildAttestation(build_id, fence["definition_hash"],
            fence["source_plan_hash"], fence["source_inventory_hash"],
            fence["header_hash"], fence["scope_hash"], fence["stage_hash"],
            fence["seed_hash"], "worker", 1)

    builder._publish_injected_typed_certificate(definition, build_id, ledger, publisher)
    assert len(called) == 1 and called[0][1] == (DAY,)
    assert len(called[0][0]["market_day_stage_certificate_v1"]) == 3
    with pytest.raises(RuntimeError, match="Keeper CAS proof"):
        builder._publish_injected_typed_certificate(
            definition, build_id, ledger, lambda *_args, **_kw: None)


def test_injected_hook_fails_before_publisher_on_partial_ledger() -> None:
    definition, build_id, _ = fixture()
    class Missing:
        def unit(self, *_):
            return None
        def seed(self, *_):
            return None
    with pytest.raises(ValueError, match="lacks completed"):
        builder._publish_injected_typed_certificate(
            definition, build_id, Missing(),
            lambda *_args, **_kw: pytest.fail("publisher reached on partial ledger"))
