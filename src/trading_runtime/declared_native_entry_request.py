"""Versioned fixed-entry request declaration, with original economics intact.

The strategy consumes this complete sealed payload. Portfolio still owns actual
quantity/cash and OMS owns execution/protection. These values describe the
existing parent's semantic request, not another sizing or fill implementation.
"""
from dataclasses import dataclass

from .declared_native_fixed_capabilities import _json, _keys

INPUT_CONTRACT = "declared-native-fixed-entry-request@1"

_INHERITED_REQUEST = {
    "policy_id": INPUT_CONTRACT,
    "action": "enter_long", "requested_quantity": 0.0,
    "capital_request": {"mode": "mandate_fraction", "fraction": [1, 3],
                        "minimum_quantity": 0.0, "maximum_quantity": None, "allow_replacement": False},
    "execution_policy": {
        "policy_id": "strategy-adaptive_urgent", "revision": 1, "name": "adaptive_urgent",
        "envelope": {"maximum_buy_price_rule": "proposal_reference_ask", "minimum_sell_price": None,
                     "deadline_ms": 750, "maximum_reprices": 4, "minimum_reprice_interval_ms": 50,
                     "persist_until_cancelled": True},
        "partial_fill_policy": "complete_remainder", "quote_source": "qmd",
    },
    "protection_profile": {
        "profile_id": "early-squeeze-fixed-stop-full-target", "revision": 1,
        "slices": [{"slice_id": "all", "quantity_fraction": [1, 1],
                    "stop": {"rule_type": "fixed_price", "order_type": "STP",
                             "price_rule": "proposal_initial_stop", "distance_percent": None,
                             "distance_bps": None, "maximum_cash_risk": None, "volatility_multiple": None,
                             "buffer_bps": 0.0, "anchor": None, "stop_limit_offset_bps": None},
                    "profit_target_price_rule": "proposal_initial_target",
                    "trailing": {"rule_type": "none", "amount": None, "percent": None,
                                 "volatility_multiple": None, "activation_gain_percent": 0.0,
                                 "breakeven_buffer_bps": 0.0, "structural_timeframe": ""},
                    "inherit_profit_target": True}],
        "add_policy": "independent_slice", "profit_pocket_transition": "keep_existing",
        "mandatory_catastrophic_backstop": True, "emergency_repair_deadline_ms": 500,
    },
    "urgency": "urgent", "time_in_force": "",
    "outside_rth_rule": "declared_extended_session_windows",
    "intent_reason": "declared_native_fixed_entry",
}


@dataclass(frozen=True, slots=True)
class DeclaredEntryRequestPolicy:
    declaration_json: str

    def __post_init__(self):
        if type(self.declaration_json) is not str or self.declaration_json != _json(_INHERITED_REQUEST):
            raise ValueError("Declared entry request differs from complete inherited economics")

    def payload(self):
        import json
        self.__post_init__()
        return json.loads(self.declaration_json)

    @property
    def policy_id(self):
        return self.payload()["policy_id"]


def inherited_fixed_entry_request_policy():
    return DeclaredEntryRequestPolicy(_json(_INHERITED_REQUEST))


def parse_declared_entry_request(value):
    _keys(value, set(_INHERITED_REQUEST), "declared entry request")
    return DeclaredEntryRequestPolicy(_json(value))
