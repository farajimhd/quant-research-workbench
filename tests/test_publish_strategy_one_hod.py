"""HOD campaign CLI is dry-run-first and protects workstation authority."""

import pytest

from scripts.clickhouse import publish_strategy_one_hod as command


def test_dry_run_does_not_connect_or_write(capsys, monkeypatch):
    monkeypatch.setattr(command, "_certified_plan", lambda **_kwargs:
                        pytest.fail("dry run connected"))
    assert command.main([]) == 0
    output = capsys.readouterr()
    assert "DRY RUN" in output.out
    assert "no connection or write" in output.out
    assert output.err == ""


def test_apply_requires_explicit_confirmation():
    with pytest.raises(SystemExit) as exc:
        command.main(["--apply"])
    assert exc.value.code == 2


def test_failure_shows_stage_without_leaking_driver_message(capsys, monkeypatch):
    monkeypatch.setattr(command.platform, "node", lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(command, "verify_session", lambda **_kwargs:
                        (_ for _ in ()).throw(ValueError("private SQL detail")))
    assert command.main(["--verify-only"]) == 1
    error = capsys.readouterr().err
    assert "ValueError at" in error
    assert "private SQL detail" not in error


def test_known_seed_coverage_gap_reports_bounded_count(capsys, monkeypatch):
    monkeypatch.setattr(command.platform, "node", lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(command, "verify_session", lambda **_kwargs:
                        (_ for _ in ()).throw(ValueError(
                            "V7 prior coverage is missing or duplicated: "
                            "missing=1 ['2026-08-18:TEST'], duplicates=0, unexpected=[]")))
    assert command.main(["--verify-only"]) == 1
    assert "missing=1" in capsys.readouterr().err


def test_recompute_ticker_compares_sealed_rows_without_writer(capsys, monkeypatch):
    from src.trading_runtime.strategy_one_hod_product import HodContext

    value = (HodContext(300_100, 100_000, 120_000, True, "r11"),)
    class Reader:
        def close(self):
            pass

    monkeypatch.setattr(command, "_plans", lambda **_kwargs:
                        ("market", "candidates", "seeds", ("TEST",)))
    monkeypatch.setattr(command, "_scope", lambda *_args, **_kwargs: "scope")
    monkeypatch.setattr(command, "readonly_clickhouse_client", lambda **_kwargs: Reader())
    monkeypatch.setattr(command, "v3_client", lambda *_args, **_kwargs: Reader())
    monkeypatch.setattr(command, "_verify_existing", lambda *_args: "attempt")
    monkeypatch.setattr(command, "_child", lambda *_args: value)
    monkeypatch.setattr(command, "_derive", lambda *_args: value)
    command.verify_recomputed_ticker(
        session_date="2026-08-19", build_id="build", ticker="TEST")
    assert "MATCH" in capsys.readouterr().out


def test_recompute_ticker_rejects_mismatched_context(monkeypatch):
    from src.trading_runtime.strategy_one_hod_product import HodContext

    class Reader:
        def close(self):
            pass

    monkeypatch.setattr(command, "_plans", lambda **_kwargs:
                        ("market", "candidates", "seeds", ("TEST",)))
    monkeypatch.setattr(command, "_scope", lambda *_args, **_kwargs: "scope")
    monkeypatch.setattr(command, "readonly_clickhouse_client", lambda **_kwargs: Reader())
    monkeypatch.setattr(command, "v3_client", lambda *_args, **_kwargs: Reader())
    monkeypatch.setattr(command, "_verify_existing", lambda *_args: "attempt")
    monkeypatch.setattr(command, "_child", lambda *_args:
                        (HodContext(300_100, 100_000, 120_000, True, "r11"),))
    monkeypatch.setattr(command, "_derive", lambda *_args:
                        (HodContext(300_100, 100_000, 120_000, False, ""),))
    with pytest.raises(RuntimeError, match="first_boundary_index=0"):
        command.verify_recomputed_ticker(
            session_date="2026-08-19", build_id="build", ticker="TEST")
