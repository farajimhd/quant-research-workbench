"""Staged exact-price OMS envelope; no legacy writer admission is implied."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Any

from .arte_journal_writer import TypedJournalBatch
from .trading_price_rows import restore_exact_price_row


@dataclass(frozen=True, slots=True)
class ExactPriceOmsStateBatch:
    base: TypedJournalBatch
    group: Mapping[str, Any]
    orders: tuple[Mapping[str, Any], ...]

    def __post_init__(self):
        if (type(self.base) is not TypedJournalBatch or len(self.base.events) != 1
                or self.base.oms_group_states or self.base.oms_order_states):
            raise ValueError("Exact OMS prices require one unmixed state envelope")
        group = restore_exact_price_row("trading_oms_group_state_v1", self.group)
        event = self.base.events[0]
        if (event['category'] != 'order_management' or event['entity_type'] != 'order_group_state'
                or event['entity_id'] != group['group_id'] or event['record_id'] != group['record_id']
                or event['account_id'] != group['account_id']
                or group['run_id'] != self.base.run_id or group['batch_id'] != self.base.batch_id
                or group['order_count'] != len(self.orders)):
            raise ValueError("Exact OMS group differs from its original event")
        ids = set()
        for ordinal, row in enumerate(self.orders):
            order = restore_exact_price_row("trading_oms_order_state_v1", row)
            if (order['ordinal'] != ordinal or order['parent_record_id'] != group['record_id']
                    or any(order[field] != group[field] for field in
                           ('run_id', 'batch_id', 'event_month', 'account_id'))
                    or order['record_id'] in ids):
                raise ValueError("Exact OMS orders differ from their group lineage")
            ids.add(order['record_id'])
        object.__setattr__(self, 'group', MappingProxyType(dict(self.group)))
        object.__setattr__(self, 'orders', tuple(MappingProxyType(dict(row)) for row in self.orders))


def seal_exact_oms_state_batch(batch):
    """Seal exact physical families with the shared event/OMS relationships.

    This returns validated rows, not a writer credential or committed prefix.
    """
    if type(batch) is not ExactPriceOmsStateBatch:
        raise ValueError("Exact OMS sealing requires its explicit envelope")
    from .arte_journal_writer import _sealed_families
    return _sealed_families(batch.base, exact_oms_price_rows=((batch.group,), batch.orders))


def decode_exact_oms_order_rows(group_row, order_rows, *, stored_utc=False):
    """Decode hashed scalar rows into native orders, without restore admission.

    A cold reader must separately prove its complete committed prefix, source
    intent, tactics, broker bindings and all remaining group components.
    """
    from .arte_oms_projection import order_request_from_oms_row
    group = restore_exact_price_row("trading_oms_group_state_v1", group_row, stored_utc=stored_utc)
    rows = tuple(restore_exact_price_row("trading_oms_order_state_v1", row,
                 stored_utc=stored_utc) for row in order_rows)
    if len(rows) != group['order_count']:
        raise ValueError("Exact OMS order count differs from its group")
    identities = set()
    for ordinal, row in enumerate(rows):
        if (row['ordinal'] != ordinal or row['parent_record_id'] != group['record_id']
                or any(row[field] != group[field] for field in
                       ('run_id', 'batch_id', 'event_month', 'account_id'))
                or row['record_id'] in identities):
            raise ValueError("Exact OMS order restoration differs from its group")
        identities.add(row['record_id'])
    return tuple(order_request_from_oms_row(group, row) for row in rows)
