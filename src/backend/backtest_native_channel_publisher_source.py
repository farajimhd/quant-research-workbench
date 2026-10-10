"""Bounded native exit publisher inputs; every image still requires cold proof."""
from dataclasses import dataclass,field

from src.trading_runtime.native_channel_management_policy import NativeChannelManagementPolicy
from src.trading_runtime.native_channel_exit_journal_context import NativeChannelExitJournalContext


@dataclass(frozen=True,slots=True)
class NativeChannelExitPublisherSource:
    run_id: str
    client: object
    manager_keeper: object
    broker_keeper: object
    policy: NativeChannelManagementPolicy
    first_price_source: object
    max_commits: int = 100_000
    _contexts: dict = field(default_factory=dict,init=False,repr=False,compare=False)

    def __post_init__(self):
        from .backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority
        if (type(self.run_id) is not str or not self.run_id
                or type(self.policy) is not NativeChannelManagementPolicy
                or getattr(self.client,'native_channel_management_policy',None)!=self.policy
                or type(self.first_price_source) is not CertifiedPriceReadbackAuthority
                or self.first_price_source.run_id!=self.run_id
                or type(self.max_commits) is not int or not 1<=self.max_commits<=100_000
                or any(not callable(getattr(reader,'read_head',None))
                    for reader in (self.manager_keeper,self.broker_keeper))):
            raise ValueError('Native channel publisher requires exact bounded reader/checkpoint inputs')
        self.policy.__post_init__()

    @property
    def contexts(self):
        return tuple(self._contexts.values())

    def _require_context(self,context):
        if (type(context) is not NativeChannelExitJournalContext or context.base.run_id!=self.run_id
                or context.policy!=self.policy or context.first_price_source is not self.first_price_source):
            raise ValueError('Native channel publisher context differs from its bound source')
        if context.batch_id not in self._contexts and len(self._contexts)>=self.max_commits:
            raise ValueError('Native channel publisher committed context capacity exhausted')

    def prepare(self,context,receipt,writer_client):
        """Cold-check the predecessor and issue the exact current-held image."""
        from .backtest_typed_publisher import TypedBacktestReceipt
        from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix,V4CommittedPrefix
        from src.trading_runtime.native_channel_exit_publication import issue_native_channel_exit_publication
        self._require_context(context)
        if type(receipt) is not TypedBacktestReceipt:
            raise ValueError('Native channel publisher requires its exact typed receipt')
        prefix=load_verified_v4_prefix(self.client,self.run_id,max_commits=self.max_commits,
            first_price_source=self.first_price_source,native_channel_contexts=self.contexts)
        if (type(prefix) is not V4CommittedPrefix or prefix.status!='running'
                or (prefix.last_sequence,prefix.last_batch_id,prefix.source_cursor)
                    !=(receipt.last_sequence,receipt.last_batch_id,receipt.source_cursor)
                or context.base.prior_batch_id!=receipt.last_batch_id
                or context.base.first_sequence!=receipt.last_sequence+1):
            raise ValueError('Native channel publisher source differs from its committed frontier')
        header=context.unit.decision
        keys=[key for key,value in context.contexts.items()
            if value.get('source_entry_intent_id')==header['source_entry_intent_id']
            and (value['current_financial'].account_id,value['current_financial'].assignment_id,
                value['current_financial'].ticker)==(header['account_id'],header['assignment_id'],header['ticker'])]
        if len(keys)!=1:
            raise ValueError('Native channel publisher lacks exactly one pending owner')
        image=issue_native_channel_exit_publication(writer_client,self.client,self.manager_keeper,
            self.broker_keeper,receipt,context,pending_record_id=keys[0],native_channel_contexts=self.contexts)
        return image,prefix

    def remember(self,context):
        """Keep source inputs after a verified receipt; cold readers reprove them."""
        self._require_context(context)
        previous=self._contexts.get(context.batch_id)
        if previous is not None and previous is not context:
            raise ValueError('Native channel publisher committed context identity changed')
        self._contexts[context.batch_id]=context

    def restore_committed_contexts(self,contexts,receipt):
        """Cold-reprove supplied restart evidence before retaining any context.

        The caller must reconstruct complete immutable source inputs. Saved
        context metadata alone grants no recovery or order permission.
        """
        from .backtest_typed_publisher import TypedBacktestReceipt
        from src.trading_runtime.native_channel_exit_journal_context import channel_contexts_by_batch
        from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix,V4CommittedPrefix
        if self._contexts or type(receipt) is not TypedBacktestReceipt:
            raise ValueError('Native history recovery requires an empty source and exact receipt')
        indexed=channel_contexts_by_batch(self.run_id,contexts,max_commits=self.max_commits)
        for context in contexts:
            self._require_context(context)
        prefix=load_verified_v4_prefix(self.client,self.run_id,max_commits=self.max_commits,
            first_price_source=self.first_price_source,native_channel_contexts=contexts)
        if (type(prefix) is not V4CommittedPrefix or prefix.status!='running'
                or prefix.run_id!=self.run_id
                or (prefix.last_sequence,prefix.last_batch_id,prefix.source_cursor)
                    !=(receipt.last_sequence,receipt.last_batch_id,receipt.source_cursor)
                or any(batch_id not in prefix.batch_ids for batch_id in indexed)
                or any(context.base.last_sequence>receipt.last_sequence for context in contexts)):
            raise ValueError('Native recovered history differs from the complete committed frontier')
        # Publish the in-memory inventory only after every cold check succeeds.
        self._contexts.update(indexed)
        return prefix
