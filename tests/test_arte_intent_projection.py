from __future__ import annotations

from dataclasses import fields, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from math import nan
from uuid import uuid4

import pytest

from src.trading_runtime.arte_intent_projection import (
    load_committed_strategy_intent_page, project_strategy_intent,
    restore_strategy_intent, strategy_intent_batch,
)
from src.trading_runtime.arte_journal_writer import (
    load_committed_prefix, publish_typed_batch,
)
from src.trading_runtime.arte_journal_projection import order_command_batch
from src.trading_runtime.execution_policies import (
    ExecutionEnvelope, ExecutionPolicy, ExecutionPolicyName,
    ProtectionProfile, ProtectionSlice, StopRule, StopRuleType,
    StopOrderType, StructuralAnchor, TrailingRule, TrailingRuleType,
)
from src.trading_runtime.signals import CapitalRequest, StrategyIntent
from src.trading_runtime.ibkr_schema import OrderRequest
from tests.test_arte_journal_writer import MemoryClient


def intent(**overrides):
    values = dict(
        intent_id="intent-1", ticker="test",
        event_time=datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc),
        action="enter_long", quantity=5.0, reference_price=12.5,
    )
    values.update(overrides)
    return StrategyIntent(**values)


def test_intent_projection_flattens_all_declared_policy_and_protection_fields():
    anchor = StructuralAnchor("level-1", 12.0,
                              datetime(2026, 8, 18, 8, 4, tzinfo=timezone.utc),
                              timeframe="1m", ordinal="latest")
    policy = ExecutionPolicy(
        policy_id="entry", revision=2, name=ExecutionPolicyName.ADAPTIVE_URGENT,
        envelope=ExecutionEnvelope(maximum_buy_price=12.75, deadline_ms=500,
                                   maximum_reprices=3, persist_until_cancelled=True),
    )
    profile = ProtectionProfile("stop", 3, (
        ProtectionSlice("first", 0.4,
                        StopRule(StopRuleType.SWING_ANCHORED, anchor=anchor),
                        profit_target_price=13.5,
                        trailing=TrailingRule(TrailingRuleType.BROKER_AMOUNT, amount=0.1)),
        ProtectionSlice("rest", 0.6, StopRule(price=11.5)),
    ))
    result = project_strategy_intent(intent(
        capital_request=CapitalRequest("fixed_quantity", 5, maximum_quantity=10),
        execution_policy=policy, protection_profile=profile,
        invalidation_price=11.5, profit_target_price=13.5,
    ))
    assert result.core["ticker"] == "test"
    assert Decimal(result.core["capital_maximum_quantity"]) == 10
    assert result.core["execution_policy_name"] == "adaptive_urgent"
    assert result.core["execution_persist_until_cancelled"] == 1
    assert result.core["protection_slice_count"] == 2
    assert result.protection_slices[0]["anchor_observation_id"] == "level-1"
    assert Decimal(result.protection_slices[0]["trailing_amount"]) == Decimal("0.1")
    assert Decimal(result.protection_slices[1]["stop_price"]) == Decimal("11.5")
    assert all(not isinstance(value, (dict, list, tuple))
               for row in (result.core, *result.protection_slices)
               for value in row.values())
    assert restore_strategy_intent(result) == intent(
        capital_request=CapitalRequest("fixed_quantity", 5, maximum_quantity=10),
        execution_policy=policy, protection_profile=profile,
        invalidation_price=11.5, profit_target_price=13.5,
    )


def test_intent_projection_rejects_unmapped_metadata_instead_of_dropping_it():
    with pytest.raises(ValueError, match="normalized typed contract"):
        project_strategy_intent(intent(metadata={"entry_body_trigger": {"price": 12.5}}))


def test_intent_projection_rejects_nonfinite_number():
    with pytest.raises(ValueError, match="non-finite"):
        project_strategy_intent(intent(reference_price=nan))


def test_intent_projection_rejects_unrepresentable_decimal():
    with pytest.raises(ValueError, match=r"Decimal\(38, 18\)"):
        project_strategy_intent(intent(reference_price=Decimal("1.1234567890123456789")))


def test_projection_field_inventory_fails_if_source_contract_expands():
    expected = {
        StrategyIntent: "intent_id ticker event_time action quantity reference_price schema_version "
                        "capital_request invalidation_price profit_target_price trailing_amount "
                        "execution_policy protection_profile urgency time_in_force outside_rth "
                        "reason metadata",
        CapitalRequest: "mode value minimum_quantity maximum_quantity allow_replacement",
        ExecutionPolicy: "policy_id revision name envelope partial_fill_policy quote_source",
        ExecutionEnvelope: "maximum_buy_price minimum_sell_price deadline_ms maximum_reprices "
                           "minimum_reprice_interval_ms persist_until_cancelled",
        ProtectionProfile: "profile_id revision slices add_policy profit_pocket_transition "
                           "mandatory_catastrophic_backstop emergency_repair_deadline_ms",
        ProtectionSlice: "slice_id quantity_fraction stop profit_target_price trailing "
                         "inherit_profit_target",
        StopRule: "rule_type order_type price distance_percent distance_bps maximum_cash_risk "
                  "volatility_multiple buffer_bps anchor stop_limit_offset_bps",
        TrailingRule: "rule_type amount percent volatility_multiple activation_gain_percent "
                      "breakeven_buffer_bps structural_timeframe",
        StructuralAnchor: "observation_id price confirmed_at timeframe ordinal",
    }
    for source, names in expected.items():
        assert {item.name for item in fields(source)} == set(names.split())


def test_restore_rejects_missing_slice_and_extra_core_field():
    profile = ProtectionProfile("stop", 1, (
        ProtectionSlice("only", 1.0, StopRule(price=11.5)),
    ))
    projected = project_strategy_intent(intent(protection_profile=profile))
    with pytest.raises(ValueError, match="missing, extra, or altered"):
        restore_strategy_intent(replace(projected, protection_slices=()))
    with pytest.raises(ValueError, match="missing, extra, or altered"):
        restore_strategy_intent(replace(projected, core={**projected.core, "unknown": 1}))


def test_restore_roundtrips_absent_optional_policy_and_full_rule_values():
    plain = intent()
    assert restore_strategy_intent(project_strategy_intent(plain)) == plain
    anchor = StructuralAnchor("level-2", 11.8,
                              datetime(2026, 8, 18, 8, 3, tzinfo=timezone.utc))
    complex_intent = intent(protection_profile=ProtectionProfile("hybrid", 1, (
        ProtectionSlice("all", 1.0, StopRule(
            StopRuleType.HYBRID, order_type=StopOrderType.STOP_LIMIT,
            distance_percent=1.5, distance_bps=150,
            maximum_cash_risk=20, volatility_multiple=2.25,
            buffer_bps=5, anchor=anchor, stop_limit_offset_bps=3,
        ), trailing=TrailingRule(
            TrailingRuleType.VOLATILITY_TRAIL, percent=1.2,
            volatility_multiple=1.7, activation_gain_percent=2,
            breakeven_buffer_bps=4, structural_timeframe="5m",
        )),
    )))
    assert restore_strategy_intent(project_strategy_intent(complex_intent)) == complex_intent


def test_intent_and_slice_publish_as_fence_verified_typed_rows():
    run_id, attempt_id, batch_id = "live:test", str(uuid4()), str(uuid4())
    profile = ProtectionProfile("single", 1, (
        ProtectionSlice("main", 1.0, StopRule(price=11.5)),
    ))
    batch = strategy_intent_batch(
        intent(protection_profile=profile), run_id=run_id,
        run_month=datetime(2026, 8, 1, tzinfo=timezone.utc).date(),
        account_id="DU1", attempt_id=attempt_id, batch_id=batch_id,
        prior_batch_id="00000000-0000-0000-0000-000000000000", sequence=1,
        source_cursor="boundary-1", run_status="running",
        recorded_at=datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc),
    )
    client = MemoryClient()
    assert publish_typed_batch(client, batch) == batch_id
    assert client.inserts == [
        "trading_event_v1", "trading_strategy_intent_v1",
        "trading_intent_protection_slice_v1", "trading_commit_v1",
    ]
    prefix = load_committed_prefix(client, run_id)
    assert prefix is not None and prefix.last_sequence == 1
    recovered = load_committed_strategy_intent_page(client, prefix)
    assert len(recovered) == 1
    assert recovered[0].intent == intent(protection_profile=profile)
    assert recovered[0].account_id == "DU1"
    assert load_committed_strategy_intent_page(client, prefix, after_sequence=1) == ()
    # ClickHouse JSONEachRow may render fixed-scale decimals without zeros.
    client.tables["trading_strategy_intent_v1"][0]["quantity"] = "5"
    client.tables["trading_intent_protection_slice_v1"][0]["quantity_fraction"] = "1"
    assert load_committed_strategy_intent_page(client, prefix)[0].intent.quantity == 5
    with pytest.raises(ValueError, match="bounds"):
        load_committed_strategy_intent_page(client, prefix, max_slices=0)
    client.tables["trading_intent_protection_slice_v1"][0]["slice_id"] = "tampered"
    with pytest.raises(RuntimeError, match="row content differs"):
        load_committed_prefix(client, run_id)
    with pytest.raises(RuntimeError, match="committed hash"):
        load_committed_strategy_intent_page(client, prefix)


def test_order_context_can_link_to_prior_committed_intent():
    run_id, attempt_id = "live:linked", str(uuid4())
    first_id, second_id = str(uuid4()), str(uuid4())
    at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    month = date(2026, 8, 1)
    first = strategy_intent_batch(
        intent(ticker="TEST"), run_id=run_id, run_month=month,
        account_id="DU1", attempt_id=attempt_id, batch_id=first_id,
        prior_batch_id="00000000-0000-0000-0000-000000000000", sequence=1,
        source_cursor="intent", run_status="running", recorded_at=at,
    )
    request = OrderRequest(acctId="DU1", conid=123, cOID="client-1",
                           ticker="TEST", orderType="LMT", side="BUY",
                           quantity=5, price=12.5)
    second = order_command_batch(
        request, run_id=run_id, run_month=month, attempt_id=attempt_id,
        batch_id=second_id, prior_batch_id=first_id, sequence=2,
        source_cursor="order", run_status="completed", command_id="cmd-1",
        created_at=at, recorded_at=at, strategy_id="strategy-1",
        strategy_revision=1, strategy_intent_id="intent-1",
        order_group_id="group-1", policy_version="policy-1",
    )
    client = MemoryClient()
    publish_typed_batch(client, first)
    publish_typed_batch(client, second)
    prefix = load_committed_prefix(client, run_id)
    assert prefix is not None and prefix.last_sequence == 2
