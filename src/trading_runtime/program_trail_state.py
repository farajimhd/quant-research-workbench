"""Trail-policy high-water projection from fills and completed program masks.

Broker quote peaks are not the searched policy's water mark. Only a valid,
age-eligible trail mask advances it. Recovery consumes the same causal fill
and source-boundary prefix; no program feature or new journal authority exists.
"""
from decimal import Decimal
from math import isfinite


class ProgramTrailState:
    def __init__(self):
        self._water = {}

    def apply_new_fill(self, fact, before, after):
        if not after.position_open:
            self._water.pop(fact.key, None)
        elif not before.position_open:
            self._water[fact.key] = fact.price

    def water_for(self, position):
        if not position.position_open:
            return Decimal(0)
        key = (position.account_id, position.strategy_id, position.assignment_id, position.listing_id)
        if key not in self._water:
            raise ValueError("Open program position lacks its causal trail water")
        return self._water[key]

    def observe(self, boundary, position, *, minimum_age_seconds):
        if boundary.close is None:
            if (position.position_open or boundary.eligible
                    or boundary.available_at > boundary.boundary_at
                    or type(boundary.rules.get("trail")) is not bool
                    or type(boundary.validity.get("trail")) is not bool):
                raise ValueError("Unobserved price conflicts with an active program position or ranking")
            return
        if (not isfinite(boundary.close) or boundary.close <= 0
                or boundary.available_at > boundary.boundary_at
                or type(boundary.rules.get("trail")) is not bool
                or type(boundary.validity.get("trail")) is not bool):
            raise ValueError("Trail projection requires a completed typed program boundary")
        age = position.age_seconds(boundary.boundary_at)
        if (position.position_open and boundary.rules["trail"] and boundary.validity["trail"]
                and age >= minimum_age_seconds):
            key = (position.account_id, position.strategy_id, position.assignment_id, position.listing_id)
            self._water[key] = max(self.water_for(position), Decimal(str(boundary.close)))
