from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "repair_qmd_live_canonical_bars.py"
SPEC = importlib.util.spec_from_file_location("repair_qmd_live_canonical_bars", SCRIPT)
assert SPEC and SPEC.loader
repair = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = repair
SPEC.loader.exec_module(repair)


def sample_range():
    return repair.RepairRange(
        event_date="2026-08-19",
        ticker="AAPL",
        duplicate_rows=32_416,
        first_sip_us=1_787_157_098_071_817,
        last_sip_us=1_787_157_683_836_227,
    )


def test_base_repair_reads_canonical_events_and_bounds_the_ticker_range() -> None:
    sql = repair.base_rebuild_sql("events", "intraday_family_bars_v2", sample_range())
    assert sql.count("FROM events FINAL") == 3
    assert "ticker = 'AAPL'" in sql
    assert "sip_timestamp_us >= 1787157098000000" in sql
    assert "sip_timestamp_us < 1787157683900000" in sql


def test_rollup_repair_reads_canonical_base_rows_and_complete_parent_buckets() -> None:
    sql = repair.rollup_rebuild_sql(
        "events", "intraday_family_bars_v2", sample_range(), 1_000_000
    )
    assert "FROM intraday_family_bars_v2 FINAL" in sql
    assert "FROM events FINAL" in sql
    assert "tuple(local_date, intDiv(bar_start_session_us, 1000000)) IN" in sql


def test_plan_artifacts_cannot_be_written_inside_repository(tmp_path: Path) -> None:
    assert repair.DEFAULT_RUNTIME_ROOT.is_absolute()
    assert "runtimes" in str(repair.DEFAULT_RUNTIME_ROOT).lower()
