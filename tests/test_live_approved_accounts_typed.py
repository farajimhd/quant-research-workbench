from copy import deepcopy

import pytest

from src.backend.live_approved_accounts_typed import (
    BINDING, MODE, PARENT, project_accounts_section, restore_accounts_section,
)


def _section():
    # Current v55 default producer emits both of these concrete binding forms.
    return {"bindings": [
        {"account_key": "replay", "name": "Backtest account",
         "source_account_id": "replay", "account_class": "simulated",
         "base_currency": "USD", "session_key": "replay",
         "portfolio_policy_id": "default", "enabled": True,
         "modes": ["replay", "backtest", "backtest_debug"]},
        {"account_key": "paper", "name": "IBKR Paper account",
         "source_account_env": "IBKR_PAPER_ACCOUNT_ID", "source_account_id": "",
         "account_class": "paper", "base_currency": "USD",
         "session_key": "ibkr-paper", "portfolio_policy_id": "default",
         "enabled": False, "system_managed": True, "modes": ["paper"]},
    ]}


def test_accounts_exact_roundtrip_named_rows_and_policy():
    section = _section()
    rows = project_accounts_section(section, configuration_revision_id="config-1")
    assert restore_accounts_section(*rows) == section
    assert rows[0]["configuration_schema_version"] == 55
    assert len(rows[1]) == 2 and len(rows[2]) == 4
    assert rows[1][0]["source_account_env_present"] is False
    assert rows[1][1]["source_account_env_present"] is True
    assert all("storage_policy = 'live_market_ssd'" in table.ddl()
               for table in (PARENT, BINDING, MODE))
    assert all("JSON" not in table.ddl() and "Object" not in table.ddl()
               for table in (PARENT, BINDING, MODE))


@pytest.mark.parametrize("mutation", ["extra", "missing", "bad_bool", "duplicate_mode"])
def test_accounts_projection_fails_closed_on_unmodeled_shape(mutation):
    section = _section()
    binding = section["bindings"][0]
    if mutation == "extra":
        binding["secret"] = "unexpected"
    elif mutation == "missing":
        del binding["base_currency"]
    elif mutation == "bad_bool":
        binding["enabled"] = 1
    else:
        binding["modes"].append("replay")
    with pytest.raises(ValueError):
        project_accounts_section(section, configuration_revision_id="config-1")


@pytest.mark.parametrize("mutation", ["hash", "missing_mode", "mode_order",
                                      "optional_presence", "duplicate_binding"])
def test_accounts_cold_read_rejects_corrupt_or_missing_rows(mutation):
    parent, bindings, modes = project_accounts_section(
        _section(), configuration_revision_id="config-1")
    parent, bindings, modes = deepcopy(parent), list(map(deepcopy, bindings)), list(map(deepcopy, modes))
    if mutation == "hash":
        parent["content_hash"] = "f" * 64
    elif mutation == "missing_mode":
        modes.pop()
    elif mutation == "mode_order":
        modes[0], modes[1] = modes[1], modes[0]
    elif mutation == "optional_presence":
        bindings[0]["source_account_env_present"] = True
    else:
        bindings[1]["account_key"] = "replay"
    with pytest.raises(ValueError):
        restore_accounts_section(parent, bindings, modes)
