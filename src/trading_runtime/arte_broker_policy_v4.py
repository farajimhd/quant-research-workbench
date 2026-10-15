"""V4 envelope for existing shared normalized broker reply-policy facts."""
from dataclasses import dataclass
from types import MappingProxyType
from src.backend.backtest_broker_policy_v3 import (
    POLICY_EVENT, MESSAGE, project_broker_reply_policy_v3, seal_broker_reply_policy_v3)


@dataclass(frozen=True, slots=True)
class V4BrokerPolicyBatch:
    base: object
    policy: object
    messages: tuple

    def __post_init__(self):
        from .arte_journal_writer import TypedJournalBatch, typed_row
        if type(self.base) is not TypedJournalBatch or self.base.status != 'running' or len(self.base.events) != 1:
            raise ValueError('Broker policy requires one exact running native event')
        policy = typed_row(POLICY_EVENT.name, {k: v for k, v in self.policy.items() if k != 'content_hash'})
        messages = tuple(typed_row(MESSAGE.name, {k: v for k, v in row.items() if k != 'content_hash'}) for row in self.messages)
        seal_broker_reply_policy_v3((policy,), messages, self.base.events,
            run_id=self.base.run_id, batch_id=self.base.batch_id)
        object.__setattr__(self, 'policy', MappingProxyType(policy))
        object.__setattr__(self, 'messages', tuple(MappingProxyType(row) for row in messages))


def broker_policy_batch_v4(record, *, run_month, attempt_id, batch_id, prior_batch_id, source_cursor):
    from .arte_journal_writer import TypedJournalBatch
    projection = project_broker_reply_policy_v3(record, attempt_id=attempt_id, batch_id=batch_id)
    base = TypedJournalBatch(record.run_id, run_month, attempt_id, batch_id, prior_batch_id,
        record.sequence, record.sequence, source_cursor, 'running', (projection.event,))
    return V4BrokerPolicyBatch(base, projection.detail, projection.messages)
