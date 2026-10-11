"""Execution-derived target-policy projection for market-only programs.

The existing normalized execution and OMS intent reason are the recovery facts.
Partial fills advance one semantic target reduction once, not once per execution.
This state is never a field available to the vectorized rule evaluator.
"""
from __future__ import annotations

from decimal import Decimal, localcontext

from .filled_position_state import FilledPosition, FillFact


class ProgramTargetState:
    def __init__(self, target_fraction):
        fraction = Decimal(str(target_fraction))
        if not fraction.is_finite() or not 0 <= fraction <= 1:
            raise ValueError("Program target policy needs its sealed fractional contract")
        self.fraction = fraction
        self._targets: dict[tuple, Decimal] = {}
        self._entry_intents: dict[tuple, str] = {}
        self._advanced_intents: dict[tuple, set[str]] = {}

    def target_for(self, position: FilledPosition) -> Decimal | None:
        if not position.position_open:
            return None
        key = (position.account_id, position.strategy_id, position.assignment_id, position.listing_id)
        if key not in self._targets:
            raise ValueError("Open program position lacks its execution-derived target")
        return self._targets[key]

    def apply_new_fill(self, fact: FillFact, before: FilledPosition, after: FilledPosition):
        """Consume only a new fill already verified by the shared execution ledger."""
        key = fact.key
        with localcontext() as context:
            context.prec = 80
            if not after.position_open:
                self._targets.pop(key, None)
                self._entry_intents.pop(key, None)
                self._advanced_intents.pop(key, None)
            elif not before.position_open:
                self._targets[key] = after.average_price * (1 + self.fraction)
                self._entry_intents[key] = fact.intent_id
                self._advanced_intents[key] = set()
            elif fact.action == "enter_long" and fact.intent_id == self._entry_intents[key]:
                # The initial acquisition can fill at several actual prices.
                self._targets[key] = after.average_price * (1 + self.fraction)
            elif fact.action == "take_profit" and fact.intent_id not in self._advanced_intents[key]:
                self._targets[key] += before.average_price * self.fraction
                self._advanced_intents[key].add(fact.intent_id)
            # Adds change actual average cost but retain the current target,
            # matching the searched policy. Ordinary reductions do not advance it.
