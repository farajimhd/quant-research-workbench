"""Exact per-lot exposure from cumulative, owned broker fill facts.

This reducer supplies repair quantities, not commands or a durability store.
Use normalized broker order/lot ownership retained by the shared OMS journal.
Never attribute all remaining group shares to the nearest target.
"""
from dataclasses import dataclass
from decimal import Context, Decimal, localcontext


@dataclass(frozen=True, slots=True)
class LadderFillFact:
    broker_order_id: str
    lot_id: str
    role: str
    cumulative_quantity: Decimal


@dataclass(frozen=True, slots=True)
class LadderLotExposure:
    lot_id: str
    acquired: Decimal
    exited: Decimal
    remaining: Decimal


def ladder_lot_exposure(lot_ids: tuple[str, ...],
                       facts: tuple[LadderFillFact, ...]) -> tuple[LadderLotExposure, ...]:
    """Snapshot reduction: repeat observations do not add shares again.

    Caller supplies each owned order exactly once at its latest causal fill
    prefix. Repair stops/targets keep the original lot ID. Shared Portfolio
    alone reconciles account cash and net broker positions.
    """
    # A fresh context also isolates rounding/traps altered by unrelated code.
    # Quantities use the normalized Decimal(38,18) journal domain. Even a
    # bounded million-order snapshot needs fewer than 80 significant digits.
    with localcontext(Context(prec=80)):
        return _reduce_exposure(lot_ids, facts)


def _reduce_exposure(lot_ids, facts):
    if (not isinstance(lot_ids, tuple) or len(lot_ids) not in (2, 3, 5)
            or any(not isinstance(x, str) or not x.strip() for x in lot_ids)
            or len(set(lot_ids)) != len(lot_ids) or not isinstance(facts, tuple)
            or len(facts) > 1_000_000):
        raise ValueError("Invalid ladder exposure ownership")
    acquired = dict.fromkeys(lot_ids, Decimal(0))
    exited = dict.fromkeys(lot_ids, Decimal(0))
    seen = set()
    for fact in facts:
        if (not isinstance(fact, LadderFillFact) or not fact.broker_order_id
                or fact.broker_order_id in seen or fact.lot_id not in acquired
                or fact.role not in {"entry", "profit_target", "protective_stop", "trailing_stop", "exit"}
                or not isinstance(fact.cumulative_quantity, Decimal)
                or not fact.cumulative_quantity.is_finite() or fact.cumulative_quantity < 0
                or fact.cumulative_quantity >= Decimal("1e20")
                or fact.cumulative_quantity.as_tuple().exponent < -18):
            raise ValueError("Invalid or duplicate owned ladder fill fact")
        seen.add(fact.broker_order_id)
        totals = acquired if fact.role == "entry" else exited
        totals[fact.lot_id] += fact.cumulative_quantity
    if any(exited[key] > acquired[key] for key in lot_ids):
        raise ValueError("Ladder exit exceeds its own acquired shares")
    return tuple(LadderLotExposure(key, acquired[key], exited[key],
                                   acquired[key] - exited[key]) for key in lot_ids)
