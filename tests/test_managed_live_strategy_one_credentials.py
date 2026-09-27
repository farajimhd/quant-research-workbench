from pathlib import Path

import pytest

from src.backend.managed_live_strategy_one_credentials import (
    load_managed_live_v4_credentials,
)


def _credential(path: Path, *, user: str = "strategy_one_live_v4_runner") -> None:
    path.write_text(
        "STRATEGY_ONE_LIVE_V4_CLICKHOUSE_URL=http://DESKTOP-SAAI85T:18123\n"
        f"STRATEGY_ONE_LIVE_V4_CLICKHOUSE_USER={user}\n"
        "STRATEGY_ONE_LIVE_V4_CLICKHOUSE_PASSWORD=" + "x" * 48 + "\n",
        encoding="utf-8",
    )


def test_managed_live_v4_loader_is_workstation_only_and_atomic(tmp_path):
    path = tmp_path / "strategy_one_live_v4_runner.env"
    env = {}
    assert not load_managed_live_v4_credentials(
        environment=env, workstation="LAPTOP", secret_path=path)
    assert not load_managed_live_v4_credentials(
        environment=env, workstation="DESKTOP-SAAI85T", secret_path=path)
    _credential(path)
    assert load_managed_live_v4_credentials(
        environment=env, workstation="DESKTOP-SAAI85T", secret_path=path)
    assert env["STRATEGY_ONE_LIVE_V4_CLICKHOUSE_USER"] == "strategy_one_live_v4_runner"
    env["STRATEGY_ONE_LIVE_V4_CLICKHOUSE_USER"] = "other"
    with pytest.raises(ValueError, match="conflicts"):
        load_managed_live_v4_credentials(
            environment=env, workstation="DESKTOP-SAAI85T", secret_path=path)
    clean = {}
    path.write_text("STRATEGY_ONE_LIVE_V4_CLICKHOUSE_URL=only\n", encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete"):
        load_managed_live_v4_credentials(
            environment=clean, workstation="DESKTOP-SAAI85T", secret_path=path)
    assert clean == {}
