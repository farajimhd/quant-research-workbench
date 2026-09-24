from pathlib import Path

import pytest

from scripts.clickhouse import provision_trading_journal as provision
from src.trading_runtime.arte_journal_schema import MARKET_READ_TABLES, TABLES


def test_grant_plan_has_only_typed_journal_inserts() -> None:
    grants = provision._grants()
    assert len(grants) == len(TABLES) + len(MARKET_READ_TABLES) + len(provision.SYSTEM_READ_TABLES)
    assert {line for line in grants if "INSERT" in line} == {
        f"GRANT SELECT, INSERT ON arte.{table.name} TO trading_journal_writer"
        for table in TABLES
    }
    assert all("INSERT ON arte.bars_v1" not in line
               and "INSERT ON arte.indicators_v1" not in line
               and "INSERT ON arte.liquidity_100ms_v1" not in line
               for line in grants)
    assert not any("arte.*" in line or " ON *.* " in line for line in grants)


def test_credential_is_reused_and_never_implicitly_rotated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    protected: list[Path] = []
    monkeypatch.setattr(provision, "_restrict_secret_file", protected.append)
    path = tmp_path / "trading_journal.env"
    first = provision._credential(path, account_exists=False)
    assert len(first) >= 40
    assert protected == [path, path]
    assert provision._credential(path, account_exists=True) == first
    assert protected == [path, path, path]
    assert path.read_text(encoding="utf-8").count(first) == 1

    path.write_text("TRADING_JOURNAL_CLICKHOUSE_USER=someone_else\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="different principal"):
        provision._credential(path, account_exists=True)


def test_empty_precreation_file_is_restart_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provision, "_restrict_secret_file", lambda _path: None)
    path = tmp_path / "trading_journal.env"
    path.touch()
    assert len(provision._credential(path, account_exists=False)) >= 40
    path.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="different principal"):
        provision._credential(path, account_exists=True)


def test_provision_refuses_wrong_host_before_any_database_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provision.platform, "node", lambda: "other-computer")
    monkeypatch.setattr(provision, "_admin_client", lambda _url: pytest.fail("database touched"))
    with pytest.raises(RuntimeError, match="DESKTOP-SAAI85T"):
        provision.provision("http://192.168.0.21:18123", apply=True)
