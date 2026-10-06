"""Complete semantic management requests, independent of strategy identity.

This preserves the inherited request terms. The consuming execution manifest
must explicitly declare this contract; it does not grant installed authority.
Portfolio and OMS remain the quantity, execution and acknowledgement owners.
"""
from dataclasses import dataclass
import json

from .declared_native_fixed_capabilities import _json, _keys

INPUT_CONTRACT = "declared-native-fixed-management-request@1"

_INHERITED_REQUEST = {
    "policy_id": INPUT_CONTRACT,
    "quantity_rule": "exact_held_financial_quantity",
    "reference_price_rule": "completed_source_bid",
    "outside_rth_rule": "declared_extended_session_windows",
    "capital_request": None,
    "protection_profile": None,
    "trailing_amount": None,
    "session_quote_source": {"resolution_ms": 100, "freshness_us": 1_000_000,
                             "price_scale": 10_000, "valid_value": 1,
                             "missing_data_rule": "no_intent"},
    "exit": {
        "action": "exit", "urgency": "urgent", "time_in_force": "DAY",
        "invalidation_price": None, "profit_target_price": None,
        "execution_policy": {
            "policy_id": "strategy-adaptive_urgent", "revision": 1,
            "name": "adaptive_urgent", "partial_fill_policy": "complete_remainder",
            "quote_source": "qmd",
            "envelope": {"maximum_buy_price": None, "minimum_sell_price": None,
                         "deadline_ms": 750, "maximum_reprices": 4,
                         "minimum_reprice_interval_ms": 50, "persist_until_cancelled": True},
        },
        "reason_rule": "own_declared_exit_kind",
        "reason_prefix": "declared_native_exit_",
    },
    "protection": {
        "urgency": "urgent", "time_in_force": "", "execution_policy": None,
        "order_rule": "target_before_stop",
        "price_rule": "replayed_transition_amendment",
        "executable_price_rule": "upward_target_above_ask_or_upward_stop_below_bid",
        "target_action": "replace_profit_target", "stop_action": "replace_protective_stop",
        "target_reason": "ordinal_resistance_target",
        "stop_reasons": ["completed_30s_bar_low", "three_resistance_step_stop"],
    },
}


@dataclass(frozen=True, slots=True)
class DeclaredManagementRequestPolicy:
    declaration_json: str

    def __post_init__(self):
        if type(self.declaration_json) is not str or self.declaration_json != _json(_INHERITED_REQUEST):
            raise ValueError("Declared management request differs from complete inherited terms")

    def payload(self):
        self.__post_init__()
        return json.loads(self.declaration_json)

    @property
    def policy_id(self):
        return self.payload()["policy_id"]


def inherited_fixed_management_request_policy():
    return DeclaredManagementRequestPolicy(_json(_INHERITED_REQUEST))


def parse_declared_management_request(value):
    _keys(value, set(_INHERITED_REQUEST), "declared management request")
    return DeclaredManagementRequestPolicy(_json(value))


def declared_management_intents(command, policy):
    """Replay the own command before projecting its declared semantic request.

    This is a pure factory. Producer readback, durable financial predecessors,
    installed approval and OMS acknowledgement remain separate authorities.
    """
    from math import isfinite
    from uuid import uuid5, NAMESPACE_URL
    from .declared_native_management_command import (
        DeclaredExitCommand, DeclaredProtectionCommand, DeclaredSessionCommand,
    )
    from .declared_native_submission import _EmptyMetadata
    from .signals import StrategyIntent
    from .execution_policies import (
        ExecutionPolicy, ExecutionPolicyName, ExecutionEnvelope, PartialFillPolicy,
    )
    from .strategy_one_position import ordered_protection_amendments
    from .entry_spread_risk import exact_epoch_us
    from src.backend.backtest_declared_base_entry_gate import DeclaredBaseEntryPolicy
    from src.backend.backtest_market_data import market_day_boundary

    if (type(policy) is not DeclaredManagementRequestPolicy
            or type(command) not in (DeclaredExitCommand, DeclaredProtectionCommand, DeclaredSessionCommand)):
        raise ValueError("Declared management projection needs exact command and policy types")
    request = policy.payload()
    replayed = command.replay()
    context = command.context
    financial = context.financial
    windows, _ = DeclaredBaseEntryPolicy(context.policy.capabilities).parameters()
    outside = any(start <= context.boundary_ms <= end for start, cutoff, end in windows)
    if not outside:
        raise ValueError("Declared management is outside authorized extended sessions")
    if (type(financial.position_quantity) not in (int, float)
            or not isfinite(financial.position_quantity) or financial.position_quantity <= 0):
        raise ValueError("Declared management needs finite positive held quantity")
    at = market_day_boundary(context.session_date, context.boundary_ms)
    if type(command) is DeclaredSessionCommand:
        if financial.pending_exit or financial.pending_entry:
            return ()
        quote_policy = request['session_quote_source']
        row = command.resolutions.get(quote_policy['resolution_ms'])
        if (row is None or type(row.get('quote_valid')) is not int
                or row.get('quote_valid') != quote_policy['valid_value']
                or type(row.get('ticker')) is not str
                or row.get('ticker') != financial.ticker or row.get('boundary_ms') != context.boundary_ms):
            return ()
        bid_int, ask_int, quote_us = (row.get(name) for name in ('bid_int', 'ask_int', 'quote_timestamp_us'))
        if (any(type(value) is not int for value in (bid_int, ask_int, quote_us))
                or not 0 < bid_int <= ask_int
                or not 0 <= exact_epoch_us(at) - quote_us <= quote_policy['freshness_us']):
            return ()
        bid = bid_int / quote_policy['price_scale']
        kind = 'session_liquidation'
    elif type(command) is DeclaredExitCommand:
        bid = command.inputs.completed.bid
        kind = replayed[0]
    else:
        bid = command.inputs.bid
        kind = 'protection'
    if type(bid) not in (int, float) or not isfinite(bid) or bid <= 0 or financial.pending_exit:
        raise ValueError("Declared management lacks an executable source bid")

    def intent(action, reason, terms, *, stop=None, target=None):
        execution = terms['execution_policy']
        if execution is not None:
            execution = dict(execution)
            execution['name'] = ExecutionPolicyName(execution['name'])
            execution['partial_fill_policy'] = PartialFillPolicy(execution['partial_fill_policy'])
            execution['envelope'] = ExecutionEnvelope(**execution['envelope'])
            execution = ExecutionPolicy(**execution)
        identity = context.policy.capabilities.identity
        key = [INPUT_CONTRACT, context.run_id, identity.strategy_id, identity.revision,
               context.source_token, context.source.intent_id, financial.account_id,
               financial.assignment_id, financial.ticker, context.boundary_ms,
               policy.declaration_json, kind, action, stop, target]
        return StrategyIntent(intent_id=str(uuid5(NAMESPACE_URL, _json(key))),
            ticker=financial.ticker, event_time=at, action=action,
            quantity=float(financial.position_quantity), reference_price=float(bid),
            capital_request=request['capital_request'], protection_profile=request['protection_profile'],
            trailing_amount=request['trailing_amount'], invalidation_price=stop,
            profit_target_price=target, execution_policy=execution,
            urgency=terms['urgency'], time_in_force=terms['time_in_force'],
            outside_rth=outside, reason=reason, metadata=_EmptyMetadata())

    if type(command) is not DeclaredProtectionCommand:
        terms = request['exit']
        return (intent(terms['action'], terms['reason_prefix'] + kind, terms,
                       stop=terms['invalidation_price'], target=terms['profit_target_price']),)
    terms = request['protection']
    result = []
    previous = command.inputs.previous
    for action, amendment in ordered_protection_amendments(replayed):
        price = amendment.get('price')
        target = action == terms['target_action']
        if (action not in (terms['target_action'], terms['stop_action'])
                or type(price) not in (int, float) or not isfinite(price)
                or price != (replayed.state.target if target else replayed.state.stop)
                or price <= (previous.target if target else previous.stop)
                or target and price <= command.inputs.ask
                or not target and not 0 < price < bid):
            raise ValueError("Declared protection amendment is not executable")
        reason = terms['target_reason'] if target else amendment.get('source')
        if not target and reason not in terms['stop_reasons']:
            raise ValueError("Declared protection has an undeclared stop source")
        result.append(intent(action, reason, terms, stop=None if target else float(price),
                             target=float(price) if target else None))
    return tuple(result)
