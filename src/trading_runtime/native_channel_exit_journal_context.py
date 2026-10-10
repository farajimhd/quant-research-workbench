"""Selected complete channel graph for cold V4 verification; no write rights."""
from dataclasses import dataclass
from collections.abc import Mapping
from types import MappingProxyType

from .arte_native_channel_exit_schema import TABLES
from .native_channel_management_policy import NativeChannelManagementPolicy, selected_exit_contracts

TABLE_NAMES=frozenset(table.name for table in TABLES)


def channel_contexts_by_batch(run_id,contexts,*,max_commits):
    if type(contexts) is not tuple or len(contexts)>max_commits:
        raise ValueError('Channel journal contexts require a complete bounded tuple')
    result={}
    for context in contexts:
        if type(context) is not NativeChannelExitJournalContext:
            raise ValueError('Channel journal context has a foreign type')
        base=context.unit.base
        if base.run_id!=run_id or base.batch_id in result:
            raise ValueError('Channel journal contexts repeat or borrow committed batch identity')
        result[base.batch_id]=context
    return result


@dataclass(frozen=True,slots=True)
class NativeChannelExitJournalContext:
    unit: object
    policy: NativeChannelManagementPolicy
    contexts: object
    first_price_source: object
    recorded_at: object
    attempt_id: str
    batch_id: str
    parent_record_id: str
    decision_record_id: str

    def __post_init__(self):
        from src.backend.backtest_native_channel_exit_unit import NativeChannelExitUnit
        if (type(self.unit) is not NativeChannelExitUnit
                or type(self.policy) is not NativeChannelManagementPolicy
                or not isinstance(self.contexts,Mapping)
                or any(not isinstance(c,Mapping) for c in self.contexts.values())):
            raise ValueError('Exact complete channel unit, policy and independent contexts required')
        self.unit.__post_init__()
        self.policy.__post_init__()
        object.__setattr__(self,'contexts',MappingProxyType({key:MappingProxyType(dict(value))
            for key,value in self.contexts.items()}))

    def verify(self,client,prefix,metadata,*,first_price_source):
        from src.backend.backtest_native_channel_exit_unit import reprove_historical_native_channel_exit_unit
        if (getattr(client,'native_channel_management_policy',None)!=self.policy
                or first_price_source is not self.first_price_source
                or not isinstance(metadata,Mapping)):
            raise ValueError('Cold channel context differs from selected client or source authority')
        base=self.unit.base
        expected=dict(run_id=base.run_id,run_month=base.run_month.isoformat(),attempt_id=base.attempt_id,
            batch_id=base.batch_id,prior_batch_id=base.prior_batch_id,
            first_sequence=base.first_sequence,last_sequence=base.last_sequence,
            source_cursor=base.source_cursor,status=base.status)
        if any(metadata.get(key)!=value for key,value in expected.items()):
            raise ValueError('Cold channel context differs from committed batch envelope')
        return reprove_historical_native_channel_exit_unit(client,prefix,self.unit,
            management_policy=self.policy,contexts=self.contexts,first_price_source=first_price_source,
            recorded_at=self.recorded_at,attempt_id=self.attempt_id,batch_id=self.batch_id,
            parent_record_id=self.parent_record_id,decision_record_id=self.decision_record_id)


def selected_cold_channel_contracts(client,run_id,batch_id,family_rows,context,*,
        prefix,metadata,first_price_source):
    required=any(row.get('family_name') in TABLE_NAMES for row in family_rows)
    if not required and context is None:
        return {}
    if (not required or type(context) is not NativeChannelExitJournalContext
            or context.unit.base.run_id!=run_id or context.unit.base.batch_id!=batch_id):
        raise ValueError('Native channel cold inventory lacks its exact complete source context')
    unit=context.verify(client,prefix,metadata,first_price_source=first_price_source)
    expected={name:len(rows) for name,rows in unit.sealed_families() if rows}
    inventory={}
    for row in family_rows:
        name,count=row.get('family_name'),row.get('row_count')
        if type(name) is not str or type(count) is not int or name in inventory:
            raise ValueError('Native channel cold inventory repeats or mistypes a family')
        inventory[name]=count
    if inventory!=expected:
        raise ValueError('Native channel cold inventory differs from complete recomputed unit')
    return {table.name:table for table in selected_exit_contracts(context.policy)}


def verify_cold_channel_graph(details,related_rows,context):
    naked=any(str(row.get('reason','')).endswith('_native_completed_channel_exit')
        for row in related_rows.get('trading_strategy_intent_v1',()))
    if context is None:
        if naked or TABLE_NAMES.intersection(details):
            raise ValueError('Native channel exit cannot recover without complete source evidence')
        return
    if type(context) is not NativeChannelExitJournalContext:
        raise ValueError('Exact native channel journal context required')
    from .arte_journal_writer import _identity
    expected={name:_identity(rows) for name,rows in context.unit.sealed_families() if rows}
    if {name:sorted(rows) for name,rows in details.items()}!=expected:
        raise ValueError('Native channel cold graph differs from complete independently reproduced unit')


def reject_unadmitted_channel_publication(families):
    """Cold evidence alone must never authorize generic journal publication."""
    if any((name in TABLE_NAMES and rows) or
            (name=='trading_strategy_intent_v1' and any(
                str(row.get('reason','')).endswith('_native_completed_channel_exit') for row in rows))
            for name,rows in families):
        raise ValueError('Native channel exit requires its dedicated fenced publication admission')
