"""Explicit audit of literal top-level state writes in the live strategy engine.

This is deliberately an incomplete *authority* inventory: nested producers
and the dynamic accepted_entry_rN family remain separate blockers. A new
literal write must be classified before typed state cutover can advance.
"""
import ast
from pathlib import Path

from src.trading_runtime.arte_assignment_state_composite import _TOP_LEVEL


KNOWN_LITERAL_KEYS = frozenset("""
accepted_entry_resistance active_stop add_step_uses adds breakout_buffer_bps
breakout_level breakout_reset_required completed_entry_macd disable_after_exit
early_stop_reentry entries entry_acquisition_exit_latched entry_at
entry_body_trigger entry_reference_price entry_target_room_retest
failure_to_extend_uses force_entry_requested gap_evidence gap_selection
high_water_price higher_low_confirmed histogram_slope_reentry_gate
historical_hod_entry historical_hod_state initial_stop initial_stop_selection
last_acceleration last_acquired_macd_episode last_command_detail
last_entry_order_cancelled last_entry_resistance last_exit_at last_exit_reason
last_exit_route_id last_extension_at last_intent_rejection last_observed_at
last_price last_profit_take_at last_profit_target_fill
last_profit_target_replaced_at last_structural_add_evaluation
last_target_candle_at latest_post_entry_swing_low
latest_structural_entry_trigger liquidation_origin_fill_role
liquidation_origin_reentry_after_fill liquidity_admission_evidence
liquidity_admitted_at low_water_price macd_closed_since
macd_histogram_history_1s manual_entry_requested manual_exit_requested
market_pressure micro_entry pending_capital_reasons pending_capital_request
pending_entry_resistance pending_profit_target_advance position_entry_level_ids
position_entry_tranches pressure_exit_latched previous_observed_price
previous_post_entry_swing_low previous_target_close profit_takes
profit_target_liquidation_required pullback_entry
qualified_entry_resistance_snapshot r1_exit r1_stop_error r3_ever_filled
reentries reentry_pullback_confirmed_at reentry_pullback_low_price
reentry_pullback_peak_price retired_entry_resistance
slope_reentry_candle_confirmation squeeze_breakout squeeze_entry
stopped_level_recovery structural_anchors structural_entry_initialized_at
structural_profit_target_frontier structural_profit_targets swing_management
swing_resistance_exit target_replenishment_armed_at
target_replenishment_level_price target_replenishment_peak_price
target_replenishment_pending target_replenishment_pending_quantity
target_replenishment_quantity target_replenishments target_resistance_snapshot
topping_tail_reentry trailing_amount trailing_support_selection
v5_entry_selection vwap_ladder_entry
""".split())


def _writes():
    source = (Path(__file__).resolve().parents[1] / "src/trading_runtime/strategy_engine.py"
              ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    literal, dynamic = set(), set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)
                and isinstance(node.value, ast.Name) and node.value.id == "state"):
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                literal.add(node.slice.value)
            else:
                dynamic.add(ast.unparse(node.slice))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "state"):
            if node.func.attr == "update":
                literal.update(arg.arg for arg in node.keywords if arg.arg)
                literal.update(key.value for value in node.args
                               if isinstance(value, ast.Dict)
                               for key in value.keys
                               if isinstance(key, ast.Constant)
                               and isinstance(key.value, str))
            elif node.func.attr in {"setdefault", "pop"} and node.args:
                key = node.args[0]
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    literal.add(key.value)
                else:
                    dynamic.add(ast.unparse(key))
    return frozenset(literal), frozenset(dynamic)


def test_every_literal_live_state_write_is_explicitly_audited():
    literal, dynamic = _writes()
    assert literal == KNOWN_LITERAL_KEYS
    # field_name is the finite reentry-pullback cleanup tuple near entry;
    # acceptance_key is parameter-derived accepted_entry_rN, and entry_key
    # selects squeeze_entry or vwap_ladder_entry after capital funding.
    assert dynamic == {"acceptance_key", "entry_key", "field_name"}
    # Coverage must remain visibly incomplete until every family is modeled.
    assert {"entries", "adds", "profit_takes"} <= _TOP_LEVEL
    assert {"entry_reference_price", "initial_stop", "active_stop"} <= _TOP_LEVEL
    assert {"last_observed_at", "last_price", "add_step_uses"} <= _TOP_LEVEL
    assert "structural_profit_targets" in _TOP_LEVEL
    assert {"vwap_ladder_entry", "squeeze_breakout"} - _TOP_LEVEL == {
            "vwap_ladder_entry"}
    assert len(literal - _TOP_LEVEL) > 50
