"""Shared exact-price command envelope, staged without writer admission."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .arte_journal_writer import TypedJournalBatch
from .trading_price_rows import restore_exact_price_row


@dataclass(frozen=True, slots=True)
class ExactPriceOrderCommandBatch:
    base: TypedJournalBatch
    command: Mapping[str, Any]

    def __post_init__(self):
        if (type(self.base) is not TypedJournalBatch or len(self.base.events) != 1
                or self.base.order_commands):
            raise ValueError("Exact order prices require one unmixed command envelope")
        command = restore_exact_price_row("trading_order_command_v1", self.command)
        event = self.base.events[0]
        if ((event['category'], event['entity_type']) not in {
                ('order_management', 'order_command'), ('command', 'order')}
                or event['record_id'] != command['record_id']
                or event['entity_id'] != command['command_id']
                or event['account_id'] != command['account_id']
                or command['run_id'] != self.base.run_id
                or command['batch_id'] != self.base.batch_id
                or command['event_month'] != event['event_month']):
            raise ValueError("Exact order command differs from its original event")
        object.__setattr__(self, 'command', MappingProxyType(dict(self.command)))


def seal_exact_order_command_batch(unit):
    """Reuse shared event/context/intent checks and emit exact physical rows."""
    if type(unit) is not ExactPriceOrderCommandBatch:
        raise ValueError("Exact command sealing requires its explicit envelope")
    from .arte_journal_writer import _sealed_families
    return _sealed_families(unit.base, exact_order_command_rows=(unit.command,))


def publish_exact_order_command_batch_v4(client, unit, *, verified_prior_prefix=None):
    """Publish through the existing V4 fence after the program writer audit."""
    if type(unit) is not ExactPriceOrderCommandBatch:
        raise ValueError("Exact command publication requires its explicit envelope")
    from .arte_journal_commit_v4 import _publish_typed_batch_v4
    return _publish_typed_batch_v4(client, unit.base, exact_order_prices=unit,
                                  verified_prior_prefix=verified_prior_prefix)
