from datetime import datetime, timezone, timedelta

import pytest

from src.trading_runtime import strategy_configuration_schema as schema
from src.trading_runtime import strategy_configuration_selection as selection

ID = "00000000-0000-0000-0000-000000000062"


def children():
    account = dict(configuration_id=ID, ordinal=0, account_key="research",
        account_class="simulated", session_key="backtest", base_currency="USD",
        enabled=1, policy_hash="a" * 64, strategy_allocation=1.)
    mode = dict(configuration_id=ID, account_key="research", ordinal=0, mode="backtest")
    group = dict(configuration_id=ID, ordinal=0, group_id="shared",
                 maximum_gross_exposure=10000., maximum_ticker_exposure=2000.)
    member = dict(configuration_id=ID, group_id="shared", ordinal=0, account_key="research")
    from tests.test_strategy_signal_configuration import rows as signal_rows
    return {**signal_rows(), schema.ACCOUNT.name: [account], schema.MODE.name: [mode], schema.MANDATE.name: [],
            schema.GROUP.name: [group], schema.GROUP_MEMBER.name: [member],
            schema.SUPERVISOR.name: [dict(configuration_id=ID, mode="backtest", enabled=1)]}


def prepare(rows=None, **changes):
    kwargs = dict(configuration_id=ID, label="Frozen programme", run_plan_id="research",
        strategy_id="columnar", strategy_revision=62, program_release_id=ID,
        program_release_hash="b" * 64,
        oms_limit_offset_bps=1.,
        published_at=datetime(2026, 10, 12, tzinfo=timezone(timedelta(hours=2))),
        children=children() if rows is None else rows)
    return selection.prepare_configuration_selection(**{**kwargs, **changes})


def test_typed_selection_roundtrip_is_immutable_and_preserves_exact_settings():
    packet = prepare()
    assert packet.header["published_at"].hour == 22
    assert packet.header["published_at"].day == 11
    assert packet.rows[schema.ACCOUNT.name][0]["policy_hash"] == "a" * 64
    assert selection.restore_configuration_selection(packet.rows) == packet
    with pytest.raises(TypeError):
        packet.rows[schema.ACCOUNT.name][0]["enabled"] = 0
    source = children()
    packet = prepare(source)
    source[schema.ACCOUNT.name][0]["policy_hash"] = "c" * 64
    assert packet.rows[schema.ACCOUNT.name][0]["policy_hash"] == "a" * 64


@pytest.mark.parametrize("failure", ["hash", "extra", "missing", "scope", "ordinal", "orphan",
                                    "duplicate", "boolean", "fraction", "nan", "supervisor"])
def test_selection_rejects_corruption_and_invalid_relationships(failure):
    rows = children()
    if failure == "hash":
        packet = prepare()
        copied = {name: [dict(row) for row in family] for name, family in packet.rows.items()}
        copied[schema.ACCOUNT.name][0]["policy_hash"] = "c" * 64
        with pytest.raises(ValueError, match="hash differs"):
            selection.restore_configuration_selection(copied)
        return
    account = rows[schema.ACCOUNT.name][0]
    if failure == "extra": account["opaque"] = "hidden"
    if failure == "missing": account.pop("base_currency")
    if failure == "scope": account["configuration_id"] = "00000000-0000-0000-0000-000000000063"
    if failure == "ordinal": account["ordinal"] = 1
    if failure == "orphan": rows[schema.GROUP_MEMBER.name][0]["account_key"] = "other"
    if failure == "duplicate": rows[schema.MODE.name].append({**rows[schema.MODE.name][0], "ordinal": 1})
    if failure == "boolean": account["enabled"] = 2
    if failure == "fraction": account["strategy_allocation"] = 1.1
    if failure == "nan": account["strategy_allocation"] = float("nan")
    if failure == "supervisor": rows[schema.SUPERVISOR.name] = []
    with pytest.raises(ValueError):
        prepare(rows)


def test_selection_rejects_ambiguous_mandates_and_unbounded_inputs():
    rows = children()
    mandate = dict(configuration_id=ID, ordinal=0, mandate_id="one", run_plan_id="research",
        account_key="research", enabled=1, maximum_cash_fraction=1.,
        maximum_planned_risk_fraction=.08, maximum_positions=3, assignment_mode="single",
        allocation_weight=1., maximum_action_authority="automatic", allow_replacement=0,
        minimum_replacement_improvement_pct=20.)
    rows[schema.MANDATE.name] = [mandate]
    assert prepare(rows).header["mandate_count"] == 1
    rows[schema.MANDATE.name].append({**mandate, "ordinal": 1, "mandate_id": "two"})
    with pytest.raises(ValueError, match="ambiguous"):
        prepare(rows)
    rows = children()
    rows[schema.ACCOUNT.name] *= 4097
    with pytest.raises(ValueError, match="bounded row budget"):
        prepare(rows)


def test_selection_orders_physical_rows_by_explicit_ordinals_and_checks_root_seal():
    rows = children()
    second = {**rows[schema.ACCOUNT.name][0], "ordinal": 1, "account_key": "second"}
    rows[schema.ACCOUNT.name].insert(0, second)
    rows[schema.MODE.name].append({**rows[schema.MODE.name][0], "account_key": "second"})
    packet = prepare(rows)
    assert [row["account_key"] for row in packet.rows[schema.ACCOUNT.name]] == ["research", "second"]
    copied = {name: [dict(row) for row in family] for name, family in packet.rows.items()}
    copied[schema.SELECTION.name][0]["program_release_hash"] = "c" * 64
    with pytest.raises(ValueError, match="seal identity or content hash"):
        selection.restore_configuration_selection(copied)
