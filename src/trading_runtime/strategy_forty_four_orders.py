"""Native order planning with explicitly owned, independent leg liquidation."""
from dataclasses import replace

from .strategy_orders import RuntimeIbkrStrategyOrderPlanner


class StrategyFortyFourOrderPlanner(RuntimeIbkrStrategyOrderPlanner):
    def __init__(self, instruments, *, run_id):
        super().__init__(instruments, strategy_id="squeeze-grid-strategy",
                         strategy_revision=44, run_id=run_id)
        self._leg_exits = {}

    def authorize_leg_exit(self, intent, *, group_id, assignment_id):
        if (intent.metadata or intent.action != "exit"
                or intent.reason != "strategy_forty_four_session_liquidation"
                or not group_id or not assignment_id):
            raise ValueError("Strategy 44 exit planner needs a named native source leg")
        source = (intent, group_id, assignment_id)
        previous = self._leg_exits.setdefault(intent.intent_id, source)
        if previous != source:
            raise ValueError("Strategy 44 leg exit identity was reused")

    def plan(self, *, intent, account_id, event):
        plan = super().plan(intent=intent, account_id=account_id, event=event)
        if intent.action == "exit":
            source = self._leg_exits.get(intent.intent_id)
            if (source is None or replace(intent, metadata={}) != source[0]
                    or intent.metadata.get("assignment_id") != source[2]):
                raise ValueError("Strategy 44 exit lacks its exact authorized native leg")
            # The native port delegates/cancels only this leg's protection.
            # The common full-ticker cancellation would cancel all siblings.
            return replace(plan, cancel_strategy_protection=False)
        if intent.action != "enter_long":
            raise ValueError("Strategy 44 planner admits only acquisition and owned liquidation")
        return plan
