"""Pure validation of a Portfolio policy-selection journal event.

The selected policy is an immutable, normalized catalogue object.  This
module resolves its content hash but never publishes it or performs I/O.
The V3 writer must attest the catalogue fence before committing an event
that references this hash.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Mapping

from src.trading_runtime.arte_journal_schema import (
    POLICY_ALLOWED_FIELDS, POLICY_BOOLEAN_FIELDS, POLICY_INTEGER_FIELDS,
    POLICY_NUMERIC_FIELDS,
)
from src.trading_runtime.journal_contract import JournalRecord
from src.trading_runtime.portfolio import PortfolioPolicy, portfolio_policy_from_payload


@dataclass(frozen=True, slots=True)
class PolicySelection:
    policy_hash: str
    policy: PortfolioPolicy
    reason: str


def project_policy_selection_v3(
    record: JournalRecord, *, account_key: str,
) -> PolicySelection:
    """Accept only the exact selected-policy payload emitted by Portfolio."""
    if ((record.category, record.entity_type) !=
            ("portfolio_management", "portfolio_control")
            or not account_key or record.entity_id != account_key
            or not record.run_id or not record.account_id):
        raise ValueError("Policy selection journal identity differs")
    payload = record.payload
    if (not isinstance(payload, Mapping)
            or set(payload) != {"event", "policy", "entries_paused", "reason",
                                    "correlation_id", "causation_id"}
            or payload["event"] != "portfolio_policy_selected"
            or payload["entries_paused"] is not True
            or not isinstance(payload["reason"], str)
            or any(not isinstance(payload[key], str) or not payload[key]
                   for key in ("correlation_id", "causation_id"))):
        raise ValueError("Policy selection payload is not exact")
    raw = payload["policy"]
    names = {field.name for field in fields(PortfolioPolicy)}
    if not isinstance(raw, Mapping) or set(raw) != names | {"identity"}:
        raise ValueError("Selected policy omits or adds typed fields")
    if (not isinstance(raw["policy_id"], str) or not raw["policy_id"]
            or type(raw["revision"]) is not int
            or any(type(raw[name]) not in (int, float) for name in POLICY_NUMERIC_FIELDS)
            or any(type(raw[name]) is not int for name in POLICY_INTEGER_FIELDS)
            or any(type(raw[name]) is not bool for name in POLICY_BOOLEAN_FIELDS)
            or any(not isinstance(raw[name], (list, tuple))
                   or any(not isinstance(item, str) or not item for item in raw[name])
                   for name in POLICY_ALLOWED_FIELDS)):
        raise ValueError("Selected policy field type differs")
    policy = portfolio_policy_from_payload(raw)
    # The policy catalogue normalizer rejects duplicate allowed values,
    # non-finite numerics, and values outside its Decimal(38,18) contract.
    # Import lazily: the catalogue publisher depends on the journal writer,
    # which will consume this projection from its worker lane.
    from src.trading_runtime.arte_portfolio_policy import _policy_rows

    policy_hash, _, _ = _policy_rows(policy)
    return PolicySelection(policy_hash, policy, payload["reason"])
