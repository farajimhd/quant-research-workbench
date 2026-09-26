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
