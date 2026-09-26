from __future__ import annotations

import pytest

from scripts.clickhouse import publish_strategy_one_candidates as subject


def test_dry_run_has_complete_safe_defaults_without_connection(capsys, monkeypatch):
    monkeypatch.setattr(subject, "certified_market_plan_from_arte", lambda **_kwargs:
                        pytest.fail("dry run accessed market data"))
    assert subject.main([]) == 0
    output = capsys.readouterr()
    assert subject.DEFAULT_DAY in output.out
    assert str(subject.FULL_SESSION_BOUNDARY_MS) in output.out
    assert "no connection or write" in output.out
    assert output.err == ""


def test_apply_rejects_non_workstation_before_market_read(capsys, monkeypatch):
    monkeypatch.setattr(subject.platform, "node", lambda: "LAPTOP")
    monkeypatch.setattr(subject, "certified_market_plan_from_arte", lambda **_kwargs:
                        pytest.fail("wrong host accessed market data"))
    assert subject.main(["--apply", "--confirm-candidate-publication"]) == 1
    assert "managed workstation" in capsys.readouterr().err


def test_reader_uses_existing_private_file_without_copying_secret(tmp_path, monkeypatch):
    path = tmp_path / "reader.env"
    path.write_text("credential-stays-here", encoding="utf-8")
    monkeypatch.setattr(subject, "_secret_path", lambda _role: path)
    checked = []
    monkeypatch.setattr(subject, "_restrict_secret_file", lambda value: checked.append(value))
    for key in ("BACKTEST_V3_READ_CREDENTIAL_FILE",
                "BACKTEST_V3_READ_CLICKHOUSE_URL",
                "BACKTEST_V3_READ_CLICKHOUSE_USER",
                "BACKTEST_V3_READ_CLICKHOUSE_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    subject._bootstrap_reader_credential()
    assert checked == [path]
    assert subject.os.environ["BACKTEST_V3_READ_CREDENTIAL_FILE"] == str(path)


@pytest.mark.parametrize("args", [
    ["--through-boundary-ms", "101"],
    ["--through-boundary-ms", "57600100"],
    ["--read-workers", "17"],
    ["--write-workers", "0"],
])
def test_invalid_campaign_scope_fails_before_connections(args):
    with pytest.raises(SystemExit):
        subject.main(args)
