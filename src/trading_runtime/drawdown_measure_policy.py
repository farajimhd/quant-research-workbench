"""Declared canonical drawdown over existing source amounts, without rounding.

Float64 source precision is preserved, not recovered. This policy changes
arithmetic and therefore requires a newly declared consuming release.
"""
from dataclasses import dataclass
from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, localcontext

POLICY_ID = "portfolio.drawdown.canonical-source-amount-difference@2"
_SCALE = Decimal("1E-18")
_CONTEXT = Context(prec=50, Emin=-999999, Emax=999999,
                   traps=[InvalidOperation, DivisionByZero, Overflow])


@dataclass(frozen=True, slots=True)
class DrawdownMeasurePolicy:
    policy_id: str = POLICY_ID

    def __post_init__(self):
        if type(self.policy_id) is not str or self.policy_id != POLICY_ID:
            raise ValueError("Unknown declared drawdown measure policy")

    def payload(self):
        return {"policy_id": self.policy_id,
                "source": "original_source_amount_shortest_decimal_lexeme",
                "arithmetic": "max(0, peak - net_liquidation)",
                "context_precision": 50, "typed_scale": 18,
                "source_peak_recovery": "unchanged_float64",
                "rounding": "none"}


def validate_drawdown_policy(policy):
    if policy is not None and type(policy) is not DrawdownMeasurePolicy:
        raise ValueError("Drawdown requires its exact declared measure policy")
    if policy is not None:
        policy.__post_init__()
    return policy


def canonical_amount(value):
    """Validate before journal handoff; unknown objects never supply lexemes."""
    if type(value) not in (int, float, Decimal):
        raise ValueError("Canonical amount requires a builtin numeric source")
    try:
        with localcontext(_CONTEXT) as context:
            amount = Decimal(str(value))
            if not amount.is_finite():
                raise ValueError("Canonical amount must be finite")
            exact = amount.quantize(_SCALE)
            if amount != exact or amount.copy_abs() >= Decimal("1E20"):
                raise ValueError("Canonical amount must fit Decimal(38,18) losslessly")
            return amount
    except (InvalidOperation, Overflow, OverflowError) as exc:
        raise ValueError("Canonical amount must fit Decimal(38,18) losslessly") from exc


def canonical_drawdown(peak, net_liquidation, *, policy):
    if validate_drawdown_policy(policy) is None:
        raise ValueError("Canonical drawdown requires a selected policy")
    peak, net = canonical_amount(peak), canonical_amount(net_liquidation)
    with localcontext(_CONTEXT) as context:
        result = max(Decimal(0), peak - net)
    return canonical_amount(result)


def drawdown_exceeds(value, limit, *, policy, inclusive=False):
    if validate_drawdown_policy(policy) is None:
        raise ValueError("Canonical comparison requires a selected policy")
    if type(inclusive) is not bool:
        raise ValueError("Drawdown comparator must be explicit")
    value, limit = canonical_amount(value), canonical_amount(limit)
    return value >= limit if inclusive else value > limit
