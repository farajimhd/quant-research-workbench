"""Prepared ladder geometry; no numbered release or order admission.

Portfolio owns the total approved quantity and cash reservation. These helpers
only construct the existing independently protected slice contract for a
surviving setup. Producers own VWAP, swings and V7 geometry. No source reads,
indicator calculations, cash mutations or broker effects occur here.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from math import isfinite, log1p

from .execution_policies import AddProtectionPolicy, ProtectionProfile, ProtectionSlice, StopRule, StopRuleType


def ladder_weights(count: int, allocation: str) -> tuple[float, ...]:
    """Normalize target-ordinal weights once per compiled policy."""
    if type(count) is not int or count not in (2, 3, 5):
        raise ValueError("Ladder requires 2, 3 or 5 protected lots")
    if allocation not in {"equal", "decreasing", "increasing"}:
        raise ValueError("Unknown ladder allocation")
    raw = tuple(1.0 if allocation == "equal" else
                1.0 / log1p(j) if allocation == "decreasing" else log1p(j)
                for j in range(1, count + 1))
    total = sum(raw)
    return tuple(value / total for value in raw)


def _positive_decimal(value: Decimal, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be a finite positive Decimal")
    return value


def percentage_ladder(entry_basis: Decimal, fractions: tuple[Decimal, ...],
                      tick: Decimal) -> tuple[Decimal, ...]:
    """Ceil percentage targets to legal ticks from an explicitly supplied basis.

    The qualified owner must supply the policy-declared fill basis. A quote
    cannot silently replace an actual-fill basis. This helper has no fill state.
    """
    _positive_decimal(entry_basis, "Entry basis")
    _positive_decimal(tick, "Tick")
    if not isinstance(fractions, tuple) or len(fractions) not in (2, 3, 5):
        raise ValueError("Percentage ladder requires a complete lot tuple")
    for value in fractions:
        _positive_decimal(value, "Target fraction")
    if any(a >= b for a, b in zip(fractions, fractions[1:])):
        raise ValueError("Target fractions must strictly increase")
    result = tuple((entry_basis * (1 + value) / tick).to_integral_value(
        rounding=ROUND_CEILING) * tick for value in fractions)
    _target_geometry(entry_basis, result)
    return result


def structural_ladder(entry_limit: Decimal, identities: tuple[str, ...],
                      lower_edges: tuple[Decimal, ...], tick: Decimal) -> tuple[Decimal, ...]:
    """Consume distinct frozen V7 identities, with one tick below each band.

    Coverage/availability and immutable producer provenance must be verified by
    the caller's preflight and source binding, not inferred by this price helper.
    """
    _positive_decimal(entry_limit, "Entry limit")
    _positive_decimal(tick, "Tick")
    if (not isinstance(identities, tuple) or not isinstance(lower_edges, tuple)
            or len(identities) not in (2, 3, 5) or len(identities) != len(lower_edges)
            or any(not isinstance(value, str) or not value.strip() for value in identities)
            or len(set(identities)) != len(identities)):
        raise ValueError("Structural ladder requires distinct complete level identities")
    for edge in lower_edges:
        _positive_decimal(edge, "Resistance lower edge")
    targets = tuple((edge / tick).to_integral_value(rounding=ROUND_FLOOR) * tick - tick
                    for edge in lower_edges)
    _target_geometry(entry_limit, targets)
    return targets


def _target_geometry(entry: Decimal, targets: tuple[Decimal, ...]) -> None:
    if any(value <= entry for value in targets) or any(
            a >= b for a, b in zip(targets, targets[1:])):
        raise ValueError("Executable targets must strictly increase above entry")


def ladder_profile(entry_basis: Decimal, stop: Decimal, targets: tuple[Decimal, ...],
                   allocation: str = "equal") -> ProtectionProfile:
    """Build one profile, not separately funded account positions or exits."""
    _positive_decimal(entry_basis, "Entry basis")
    _positive_decimal(stop, "Stop")
    if stop >= entry_basis or not isinstance(targets, tuple):
        raise ValueError("A complete ladder requires a stop below entry")
    weights = ladder_weights(len(targets), allocation)
    for value in targets:
        _positive_decimal(value, "Target")
    _target_geometry(entry_basis, targets)
    prices = (float(stop), *(float(value) for value in targets))
    if not all(isfinite(value) and value > 0 for value in prices):
        raise ValueError("Protection prices exceed the finite native price domain")
    if float(stop) >= float(entry_basis) or any(
            float(a) >= float(b) for a, b in zip((entry_basis, *targets), targets)):
        raise ValueError("Native price conversion collapsed ladder geometry")
    return ProtectionProfile("early-squeeze-ladder-prepared", 1, tuple(
        ProtectionSlice(f"lot-{index + 1}", fraction,
            StopRule(StopRuleType.FIXED_PRICE, price=float(stop)),
            profit_target_price=float(target), inherit_profit_target=False)
        for index, (fraction, target) in enumerate(zip(weights, targets, strict=True))),
        add_policy=AddProtectionPolicy.INDEPENDENT_FIXED_LOTS)
