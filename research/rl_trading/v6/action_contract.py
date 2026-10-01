"""Versioned identity-token axes for WAIT and ticker-specific HOLD.

Keep existing order-token offsets: append HOLD slots after target slots.
WAIT and HOLD have no execution outcome or conditional continuous parameter.
Consumers must explicitly adopt this version; old portfolio HOLD labels are
not silently treated as ticker-specific HOLD supervision.
"""
from dataclasses import dataclass

ACTION_VERSION = 'rl-v6-wait-held-ticker-hold-v1'
ACTION_NAMES = ('wait', 'enter_long', 'exit_long', 'set_stop', 'set_target', 'hold')


@dataclass(frozen=True)
class ActionAxes:
    listings: int
    holdings: int

    def __post_init__(self):
        if (type(self.listings) is not int or type(self.holdings) is not int
                or self.listings < 1 or not 0 <= self.holdings <= self.listings):
            raise ValueError('Invalid listing/holding action axes')

    @property
    def width(self):
        return 1 + self.listings + 4 * self.holdings

    @property
    def hold_base(self):
        return 1 + self.listings + 3 * self.holdings

    def action_class(self, token):
        if type(token) is not int or not 0 <= token < self.width:
            raise ValueError('Invalid action token')
        if token == 0:
            return 0
        if token <= self.listings:
            return 1
        return 2 + (token - 1 - self.listings) // self.holdings

    def held_slot(self, token):
        if self.action_class(token) < 2:
            raise ValueError('Action does not target a held listing')
        return (token - 1 - self.listings) % self.holdings

    def parameter_kind(self, token):
        action = self.action_class(token)
        return 1 if action == 1 else 2 if action in (3, 4) else 0

    def execution_action(self, token):
        """OMS action ids remain unchanged; both no-order actions map to zero."""
        action = self.action_class(token)
        return action if 1 <= action <= 4 else 0
