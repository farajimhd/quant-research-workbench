"""Explicit sealed risk selection and pure V19 acquisition rejection gate.

This module has no database or broker side effects. A successful check certifies
only the supplied price/quantity snapshot; admission must recheck changed prices.
"""
from dataclasses import dataclass
from decimal import Decimal, localcontext
from types import MappingProxyType

from .arte_journal_schema import TableContract
from .strategy_configuration_selection import _hash, _row


RISK = TableContract("trading_configuration_execution_risk_v1", (
    ("configuration_id", "UUID"), ("configuration_hash", "FixedString(64)"),
    ("maximum_stop_distance", "Float64"), ("maximum_position_risk", "Float64"),
    ("fee_per_share", "Float64"), ("minimum_order_fee", "Float64"),
    ("cost_bps", "Float64"), ("content_hash", "FixedString(64)"),
    ("published_at", "DateTime64(6, 'UTC')")), "tuple()", "configuration_id")
TABLES = (RISK,)


def is_risk_rejection_status(status):
    if type(status) is not str or not status.startswith('risk_rejected:'):
        return False
    reasons = status.removeprefix('risk_rejected:').split(',')
    allowed = {'entry_stop_not_below_price', 'maximum_stop_distance_exceeded',
               'maximum_position_risk_exceeded'}
    return bool(reasons) and len(reasons) == len(set(reasons)) and set(reasons) <= allowed


def execution_risk_schema_plan(*, reader_principals, publisher_principal):
    """Reviewable DDL/grants only; principals must be explicitly supplied."""
    import re
    principals = tuple(reader_principals)
    if (not principals or any(type(p) is not str or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p) is None
            for p in (*principals, publisher_principal))):
        raise ValueError("Risk schema plan requires explicit safe principal identities")
    return (RISK.ddl(), *(f"GRANT SELECT ON arte.{RISK.name} TO {p}" for p in principals),
            f"GRANT SELECT, INSERT ON arte.{RISK.name} TO {publisher_principal}")


def bind_execution_risk(revision, contract):
    from copy import deepcopy
    if type(contract) is not ConfigurationExecutionRisk or type(revision) is not dict:
        raise TypeError("Risk binding requires a typed contract and materialized selection")
    contract = restore_execution_risk(contract.rows)
    h = contract.header
    if (revision.get("revision_id") != "strategy-configuration:" + h["configuration_id"]
            or revision.get("selection_hash") != h["configuration_hash"]
            or revision.get("execution_risk_hash") not in (None, h["content_hash"])):
        raise ValueError("Execution risk differs from selected configuration")
    result = deepcopy(revision)
    result["execution_risk_hash"] = h["content_hash"]
    # Existing saved-run configuration hashes cover payload. Pin the separate
    # risk authority there too so cold recovery cannot adopt a newer approval.
    if 'payload' in result:
        prior = result['payload'].setdefault('oms', {}).get('execution_risk_hash')
        if prior not in (None, h['content_hash']):
            raise ValueError('Saved execution risk differs from approved configuration')
        result['payload']['oms']['execution_risk_hash'] = h['content_hash']
        result['content_hash'] = _hash(result['payload'])
    return result


@dataclass(frozen=True, slots=True)
class ConfigurationExecutionRisk:
    header: object
    rows: object


def project_execution_risk(*, configuration_id, configuration_hash,
        maximum_stop_distance, maximum_position_risk, fee_per_share,
        minimum_order_fee, cost_bps, published_at):
    row = dict(configuration_id=configuration_id, configuration_hash=configuration_hash,
        maximum_stop_distance=maximum_stop_distance, maximum_position_risk=maximum_position_risk,
        fee_per_share=fee_per_share, minimum_order_fee=minimum_order_fee,
        cost_bps=cost_bps, published_at=published_at)
    row["content_hash"] = _hash({k: v for k, v in row.items() if k != "published_at"})
    return restore_execution_risk({RISK.name: [row]})


def restore_execution_risk(source):
    if set(source) != {RISK.name} or len(source[RISK.name]) != 1:
        raise ValueError("Execution risk requires exactly one normalized seal")
    row = _row(RISK, source[RISK.name][0])
    if (not 0 < row["maximum_stop_distance"] <= .2
            or not 0 < row["maximum_position_risk"] <= 300
            or any(row[key] < 0 for key in ('fee_per_share', 'minimum_order_fee', 'cost_bps'))
            or _hash({k: v for k, v in row.items()
                      if k not in {"content_hash", "published_at"}}) != row["content_hash"]):
        raise ValueError("Execution risk envelope or integrity differs")
    header = MappingProxyType(row)
    return ConfigurationExecutionRisk(header, MappingProxyType({RISK.name: (header,)}))


@dataclass(frozen=True, slots=True)
class AcquisitionRiskDecision:
    allowed: bool
    reasons: tuple[str, ...]
    prospective_quantity: Decimal
    prospective_basis: Decimal
    modeled_stop_loss: Decimal


def evaluate_acquisition_risk(contract: ConfigurationExecutionRisk, *, action: str,
        quantity: Decimal, average_price: Decimal, stop_price: Decimal,
        episode_paid_fees: Decimal, episode_realized_gross_pnl: Decimal,
        requested_cash: Decimal, execution_price: Decimal) -> AcquisitionRiskDecision:
    """Reject rather than resize or move stops; fees/losses belong to this episode.

    Quantity is actual surviving filled quantity, excluding pending orders.
    Callers must block concurrent pending acquisitions. An entry has zero prior
    quantity and episode accounting. Realized profits never subsidize risk.
    """
    if type(contract) is not ConfigurationExecutionRisk:
        raise TypeError("Risk gate requires an explicit normalized contract")
    contract = restore_execution_risk(contract.rows)
    values = (quantity, average_price, stop_price, episode_paid_fees,
              episode_realized_gross_pnl, requested_cash, execution_price)
    if any(type(v) is not Decimal or not v.is_finite() or abs(v) >= Decimal("1e28")
           or v.as_tuple().exponent < -28 for v in values):
        raise ValueError("Risk gate requires bounded finite typed Decimal inputs")
    if (action not in {"enter_long", "add_long"} or quantity < 0
            or episode_paid_fees < 0 or requested_cash <= 0 or execution_price <= 0
            or stop_price <= 0 or average_price < 0
            or action == "enter_long" and any(v != 0 for v in
                (quantity, average_price, episode_paid_fees, episode_realized_gross_pnl))
            or action == "add_long" and (quantity <= 0 or average_price <= 0)):
        raise ValueError("Risk gate acquisition state is inconsistent")
    h = {k: Decimal(str(v)) for k, v in contract.header.items()
         if k in {"maximum_stop_distance", "maximum_position_risk", "fee_per_share",
                  "minimum_order_fee", "cost_bps"}}
    with localcontext() as context:
        context.prec = 120
        bought = requested_cash / execution_price
        prospective = quantity + bought
        basis = quantity * average_price + requested_cash
        cost = h["cost_bps"] / 10000
        fee = lambda q: max(q * h["fee_per_share"], h["minimum_order_fee"])
        if action == "enter_long":
            risk = bought * max(execution_price - stop_price, Decimal(0)) + 2 * fee(bought)
            risk += requested_cash * cost + bought * stop_price * cost
        else:
            risk = max(quantity * (average_price - stop_price)
                       + bought * (execution_price - stop_price), Decimal(0))
            risk += max(episode_paid_fees - episode_realized_gross_pnl, Decimal(0))
            risk += fee(bought) + requested_cash * cost + fee(prospective)
            risk += prospective * stop_price * cost
        reasons = []
        if action == "enter_long" and stop_price >= execution_price:
            reasons.append("entry_stop_not_below_price")
        # Preserve the evaluator's declared 1e-12 distance tolerance exactly.
        if prospective * stop_price < (1 - h["maximum_stop_distance"] - Decimal("1e-12")) * basis:
            reasons.append("maximum_stop_distance_exceeded")
        if risk > h["maximum_position_risk"]:
            reasons.append("maximum_position_risk_exceeded")
        return AcquisitionRiskDecision(not reasons, tuple(reasons), prospective, basis, risk)
