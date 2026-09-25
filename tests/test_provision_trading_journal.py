from pathlib import Path
import os

import pytest

from scripts.clickhouse import provision_trading_journal as provision
from src.trading_runtime.arte_journal_schema import (
    MARKET_READ_TABLES, TABLES, fixed_backtest_v2_contracts,
)


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
    assert not any("arte.bt_" in line for line in grants)
    assert "GRANT SELECT, INSERT ON arte.trading_backtest_cursor_v1 TO trading_journal_writer" in grants


def test_fixed_v2_grants_revoke_legacy_inserts_and_exclude_market_writes() -> None:
    grants = provision._grants(fixed_backtest_v2=True)
    legacy = {"trading_strategy_signal_v1", "trading_commit_v1"}
    assert {line for line in grants if line.startswith("REVOKE ")} == {
        f"REVOKE INSERT ON arte.{name} FROM trading_journal_writer"
        for name in legacy
    }
    assert {line for line in grants if line.startswith("GRANT INSERT ")} == {
        f"GRANT INSERT ON arte.{table.name} TO trading_journal_writer"
        for table in fixed_backtest_v2_contracts() if table.name not in legacy
    }
    assert all("GRANT INSERT ON arte.bars_v1" not in line
               and "GRANT INSERT ON arte.indicators_v1" not in line
               and "GRANT INSERT ON arte.liquidity_100ms_v1" not in line
               for line in grants)
    with pytest.raises(ValueError, match="cannot be combined"):
        provision._grants(fixed_backtest_v2=True, staged_live_signal=True)


def test_fixed_v2_apply_rejects_missing_layout_before_credentials_or_grants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Admin:
        calls = []
        def execute(self, sql):
            self.calls.append(sql)
            assert sql.startswith("SELECT count() FROM system.users")
            return "1\n"
    admin = Admin()
    monkeypatch.setattr(provision.platform, "node", lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(provision, "SECRET_ROOT", tmp_path)
    monkeypatch.setattr(provision, "_admin_client", lambda _url: admin)
    monkeypatch.setattr(provision, "storage_preflight",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            ValueError("missing typed V2 table")))
    monkeypatch.setattr(provision, "_credential",
                        lambda *_args, **_kwargs: pytest.fail("credential touched"))
    with pytest.raises(ValueError, match="missing typed V2 table"):
        provision.provision("http://DESKTOP-SAAI85T:18123", apply=True,
                            fixed_backtest_v2=True)
    assert len(admin.calls) == 1


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
    assert "TRADING_JOURNAL_CLICKHOUSE_URL=http://DESKTOP-SAAI85T:18123\n" in path.read_text(encoding="utf-8")

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


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL contract")
def test_private_acl_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "empty.env"
    path.touch()
    provision._restrict_secret_file(path)
    provision._restrict_secret_file(path)


def test_provision_refuses_wrong_host_before_any_database_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provision.platform, "node", lambda: "other-computer")
    monkeypatch.setattr(provision, "_admin_client", lambda _url: pytest.fail("database touched"))
    with pytest.raises(RuntimeError, match="DESKTOP-SAAI85T"):
        provision.provision("http://DESKTOP-SAAI85T:18123", apply=True)
