"""Shared exact fill envelope; projection and sealing grant no publication."""
from dataclasses import dataclass
from types import MappingProxyType
from collections.abc import Mapping
from decimal import Decimal

from .arte_journal_writer import TypedJournalBatch, _canonical_typed_content
from .trading_price_rows import restore_exact_price_row


@dataclass(frozen=True, slots=True)
class ExactPriceExecutionBatch:
    base: TypedJournalBatch
    execution: Mapping

    def __post_init__(self):
        if (type(self.base) is not TypedJournalBatch or len(self.base.events) != 1
                or self.base.executions):
            raise ValueError("Exact fill prices require one unmixed execution envelope")
        execution = restore_exact_price_row("trading_execution_v1", self.execution)
        event = self.base.events[0]
        canonical_event = _canonical_typed_content("trading_event_v1",
            {key: value for key, value in event.items() if key != "content_hash"})
        if ((event["category"], event["entity_type"]) != ("execution", "fill")
                or event["record_id"] != execution["record_id"]
                or event["entity_id"] != execution["execution_id"]
                or event["account_id"] != execution["account_id"]
                or execution["run_id"] != self.base.run_id
                or execution["batch_id"] != self.base.batch_id
                or execution["event_month"] != event["event_month"]
                or execution["source_event_time"] != canonical_event["event_time"]
                or execution["received_at"] != canonical_event["recorded_at"]
                or execution["price"] <= 0 or Decimal(execution["quantity"]) <= 0):
            raise ValueError("Exact fill differs from its original event")
        object.__setattr__(self, "execution", MappingProxyType(dict(self.execution)))


def seal_exact_execution_batch(unit):
    """Preserve shared event/financial checks while sealing the exact family."""
    if type(unit) is not ExactPriceExecutionBatch:
        raise ValueError("Exact execution sealing requires its explicit envelope")
    from .arte_journal_writer import _sealed_families
    return _sealed_families(unit.base, exact_execution_rows=(unit.execution,))
