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
    combined = set(provision._grants(
        fixed_backtest_v2=True, staged_live_plan_membership=True))
    assert combined - set(grants) == {
        f"GRANT SELECT, INSERT ON arte.{table.name} TO trading_journal_writer"
        for table in (*provision.LIVE_PLAN_MEMBERSHIP_TABLES,
                      provision.STRATEGY_ONE_ASSIGNMENT_TABLE)
    }


def test_staged_live_membership_adds_only_typed_control_plane_grants() -> None:
    base = set(provision._grants())
    extended = set(provision._grants(staged_live_plan_membership=True))
    assert extended - base == {
        f"GRANT SELECT, INSERT ON arte.{table.name} TO trading_journal_writer"
        for table in (*provision.LIVE_PLAN_MEMBERSHIP_TABLES,
                      provision.STRATEGY_ONE_ASSIGNMENT_TABLE)
    }
    assert not any("INSERT ON arte.bars_v1" in grant for grant in extended)


def test_workstation_local_clickhouse_endpoint_is_allowed_without_weaker_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Admin:
        def execute(self, sql):
            assert sql.startswith("SELECT count() FROM system.users")
            return "1\n"
    seen = []
    monkeypatch.setattr(provision.platform, "node", lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(provision, "SECRET_ROOT", tmp_path)
    monkeypatch.setattr(provision, "_admin_client",
                        lambda url: (seen.append(url), Admin())[1])
    provision.provision("http://127.0.0.1:8123", apply=False,
                        staged_live_plan_membership=True)
    assert seen == ["http://127.0.0.1:8123"]
    with pytest.raises(RuntimeError, match="Unexpected ClickHouse endpoint"):
        provision.provision("http://127.0.0.1:18123", apply=False)


def test_signal_only_plan_does_not_reconcile_legacy_grants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Admin:
        def execute(self, sql):
            assert sql.startswith("SELECT count() FROM system.users")
            return "1\n"
    monkeypatch.setattr(provision.platform, "node", lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(provision, "SECRET_ROOT", tmp_path)
    monkeypatch.setattr(provision, "_admin_client", lambda _url: Admin())
    provision.provision("http://127.0.0.1:8123", apply=False,
                        staged_live_signal_only=True)
    with pytest.raises(ValueError, match="cannot be combined"):
        provision.provision("http://127.0.0.1:8123", apply=False,
                            staged_live_signal_only=True,
                            fixed_backtest_v2=True)


def test_oms_tactic_grants_are_opt_in_and_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.trading_runtime.arte_oms_tactic_schema import TABLES

    class Admin:
        def __init__(self):
            self.calls = []

        def execute(self, sql):
            self.calls.append(sql)
            if sql.startswith("SELECT count() FROM system.users"):
                return "1\n"
            return ""

    class Writer:
        def __init__(self, *_args, **_kwargs):
            pass

        def execute(self, sql):
            assert sql == "SELECT currentUser()"
            return provision.PRINCIPAL

    admin = Admin()
    checked = []
    monkeypatch.setattr(provision.platform, "node", lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(provision, "SECRET_ROOT", tmp_path)
    monkeypatch.setattr(provision, "_admin_client", lambda _url: admin)
    monkeypatch.setattr(provision, "_credential", lambda *_args, **_kwargs: "private")
    monkeypatch.setattr(provision, "ClickHouseHttpClient", Writer)
    monkeypatch.setattr(provision, "storage_preflight",
                        lambda _client, *, tables: checked.append(tables))
    monkeypatch.setattr(provision, "fixed_backtest_v2_preflight",
                        lambda _writer: None)
    provision.provision("http://127.0.0.1:8123", apply=False,
                        oms_execution_tactic_only=True)
    assert len(admin.calls) == 1
    provision.provision("http://127.0.0.1:8123", apply=True,
                        oms_execution_tactic_only=True)
    assert admin.calls[2:] == [
        f"GRANT SELECT, INSERT ON arte.{table.name} TO {provision.PRINCIPAL}"
        for table in TABLES]
    assert checked == [TABLES, TABLES]


def test_strategy_one_live_read_grants_are_three_exact_selects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Admin:
        def __init__(self):
            self.calls = []

        def execute(self, sql):
            self.calls.append(sql)
            if sql.startswith("SELECT count() FROM system.users"):
                return "1\n"
            assert sql in provision._strategy_one_live_read_grants()
            return ""

    class Reader:
        def __init__(self, *_args, **_kwargs):
            self.calls = []

        def execute(self, sql):
            self.calls.append(sql)
            if sql == "SELECT currentUser()":
                return provision.PRINCIPAL
            assert sql == "SELECT approval_id FROM arte.live_strategy_one_approval_v1 LIMIT 0"
            return ""

    admin = Admin()
    reader = Reader()
    checked = []
    monkeypatch.setattr(provision.platform, "node", lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(provision, "SECRET_ROOT", tmp_path)
    monkeypatch.setattr(provision, "_admin_client", lambda _url: admin)
    monkeypatch.setattr(provision, "_credential", lambda *_args, **_kwargs: "private")
    monkeypatch.setattr(provision, "ClickHouseHttpClient", lambda *_args, **_kwargs: reader)
    monkeypatch.setattr(provision, "verify_strategy_one_configuration_tables",
                        lambda client: checked.append(("config", client)))
    monkeypatch.setattr(provision, "storage_preflight",
                        lambda client, *, tables: checked.append(("approval", client, tables)))
    monkeypatch.setattr(provision, "certify_strategy_one_configuration",
                        lambda client: checked.append(("release", client)))
    monkeypatch.setattr(provision, "fixed_backtest_v2_preflight",
                        lambda client: checked.append(("journal", client)))
    grants = provision._strategy_one_live_read_grants()
    assert len(grants) == 3 and all(
        grant.startswith("GRANT SELECT ON arte.") and "INSERT" not in grant
        for grant in grants)
    provision.provision("http://127.0.0.1:8123", apply=False,
                        strategy_one_live_read_only=True)
    assert len(admin.calls) == 1
    provision.provision("http://127.0.0.1:8123", apply=True,
                        strategy_one_live_read_only=True)
    assert admin.calls[2:] == list(grants)
    assert checked == [
        ("config", admin), ("approval", admin, (provision.STRATEGY_ONE_APPROVAL_TABLE,)),
        ("config", reader), ("approval", reader, (provision.STRATEGY_ONE_APPROVAL_TABLE,)),
        ("release", reader), ("journal", reader),
    ]
    with pytest.raises(ValueError, match="cannot be combined"):
        provision.provision("http://127.0.0.1:8123", apply=False,
                            strategy_one_live_read_only=True,
                            fixed_backtest_v2=True)


def test_signal_only_apply_preserves_existing_v2_grant_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Admin:
        def __init__(self):
            self.calls = []
        def execute(self, sql):
            self.calls.append(sql)
            if sql.startswith("SELECT count() FROM system.users"):
                return "1\n"
            assert sql in provision.staged_grants(provision.PRINCIPAL)
            return ""
    class Writer:
        def __init__(self, *_args, **_kwargs):
            pass
        def execute(self, sql):
            assert sql == "SELECT currentUser()"
            return provision.PRINCIPAL
    admin = Admin()
    audits = []
    monkeypatch.setattr(provision.platform, "node", lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(provision, "SECRET_ROOT", tmp_path)
    monkeypatch.setattr(provision, "_admin_client", lambda _url: admin)
    monkeypatch.setattr(provision, "_credential", lambda *_args, **_kwargs: "private")
    monkeypatch.setattr(provision, "ClickHouseHttpClient", Writer)
    monkeypatch.setattr(provision, "staged_live_signal_storage_preflight",
                        lambda _client: None)
    monkeypatch.setattr(provision, "fixed_backtest_v2_preflight",
                        lambda _client: audits.append("verified"))
    provision.provision("http://127.0.0.1:8123", apply=True,
                        staged_live_signal_only=True)
    assert audits == ["verified", "verified"]
    assert admin.calls[1:] == list(provision.staged_grants(provision.PRINCIPAL))


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


def test_live_membership_apply_checks_layout_before_credentials_or_grants(
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
                            ValueError("missing typed membership table")))
    monkeypatch.setattr(provision, "_credential",
                        lambda *_args, **_kwargs: pytest.fail("credential touched"))
    with pytest.raises(ValueError, match="missing typed membership table"):
        provision.provision("http://DESKTOP-SAAI85T:18123", apply=True,
                            staged_live_plan_membership=True)
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
