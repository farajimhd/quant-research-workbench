from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "warm_indicator_history.py"
SPEC = importlib.util.spec_from_file_location("warm_indicator_history", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_session_start_uses_new_york_dst() -> None:
    assert MODULE.session_start_utc(date(2026, 8, 21)) == "2026-08-21T08:00:00+00:00"


def test_explicit_tickers_are_normalized_without_universe_query() -> None:
    assert MODULE.load_tickers("http://unused", date(2026, 8, 21), ["sugp", " SUGP ", "juns"]) == [
        "JUNS",
        "SUGP",
    ]


def test_manifest_summary_does_not_duplicate_bar_payload() -> None:
    summary = MODULE.result_summary(
        {
            "status": "ready",
            "required_bars": 200,
            "bars": [{"bar_start": "2026-08-20T20:00:00Z", "close": 3.4}] * 200,
            "source_revision": {"token": "revision"},
        }
    )
    assert summary["bar_count"] == 200
    assert "bars" not in summary
