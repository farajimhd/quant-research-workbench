"""Producer CLI never touches market inputs in plan-only mode."""
from datetime import date
import json
from pathlib import Path

import pytest

from scripts.clickhouse import build_liquidity_execution_prices as command


def test_plan_only_reports_certified_units_without_opening_clickhouse(monkeypatch, capsys):
    monkeypatch.setattr(command, "_plan", lambda *_: (
        [("ABCD", "00000000-0000-0000-0000-000000000001", 100)], []))
    monkeypatch.setattr(command, "_admin_client", lambda *_: pytest.fail(
        "Plan-only must not connect to ClickHouse"))
    result = command.run(runtime=Path("D:/TradingML/runtimes"),
                         build_id="build", day=date(2026, 8, 18),
                         workers=4, apply=False)
    assert result == {"total": 1, "completed": 0, "skipped": 0,
                      "failed": 0, "created_tables": 0}
    assert "Plan only: no ClickHouse connection or write" in capsys.readouterr().out


def test_apply_needs_explicit_confirmation(capsys):
    with pytest.raises(SystemExit) as error:
        command.main(["--build-id", "build", "--date", "2026-08-18", "--apply"])
    assert error.value.code == 2
    assert "--confirm-eligible-price-publication" in capsys.readouterr().err


def test_layout_refuses_wrong_policy_before_ddl():
    class Client:
        def execute(self, query):
            assert query.startswith("SELECT disks FROM system.storage_policies")
            return json.dumps({"disks": ["default"]})
    with pytest.raises(RuntimeError, match="SSD-only"):
        command._layout(Client(), apply=True)
