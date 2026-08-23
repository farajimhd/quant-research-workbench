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
    return repair.DuplicateRange(
        event_date="2026-08-19",
        ticker="AAPL",
        duplicate_rows=32_416,
        first_sip_us=1_787_157_098_071_817,
        last_sip_us=1_787_157_683_836_227,
    )


def test_duplicate_audit_preserves_bounded_bucket_range() -> None:
    item = sample_range()
    assert item.start_us == 1_787_157_098_000_000
    assert item.end_us == 1_787_157_683_900_000


def test_duplicate_audit_groups_full_event_identity_without_writing_bars() -> None:
    sql = repair.duplicate_ranges_sql("events", "2026-08-19", "2026-08-21")
    assert "FROM events" in sql
    assert "HAVING copies > 1" in sql
    assert "INSERT" not in sql


def test_identifiers_fail_closed() -> None:
    assert repair.identifier("intraday_family_bars_v3") == "intraday_family_bars_v3"
    try:
        repair.identifier("events; DROP TABLE events")
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe identifier was accepted")


def test_plan_artifacts_cannot_be_written_inside_repository(tmp_path: Path) -> None:
    assert repair.DEFAULT_RUNTIME_ROOT.is_absolute()
    assert "runtimes" in str(repair.DEFAULT_RUNTIME_ROOT).lower()
