"""Attested v5 inputs for inherited management; never order authority."""
from dataclasses import dataclass

from src.trading_runtime.native_channel_management_policy import NativeChannelManagementPolicy
from src.trading_runtime.native_channel_manager_reader import load_attested_native_channel_manager_snapshot


@dataclass(frozen=True, slots=True)
class _NativeHistoryReader:
    _reader: object
    _publisher: object
    _source: object
    _history: tuple

    def _require(self):
        if (self._publisher._native_channel_exit_source is not self._source
                or self._publisher._first_price_source is not self._source.first_price_source
                or self._publisher.journal.run_id != self._source.run_id
                or getattr(self._reader,'native_channel_management_policy',None) != self._source.policy
                or self._source.contexts != self._history):
            raise ValueError('Native inherited checkpoint history binding changed')

    @property
    def native_channel_checkpoint_history(self):
        self._require()
        return self._history

    def __getattr__(self,name):
        self._require()
        return getattr(self._reader,name)


def bind_native_inherited_checkpoint_history(reader, *, publisher, run_id):
    """Carry exact publisher history through a SELECT-only confirmation read."""
    policy=getattr(reader,'native_channel_management_policy',None)
    if policy is None:
        return reader
    from .backtest_typed_publisher import BacktestTypedJournalPublisher
    from .backtest_native_channel_publisher_source import NativeChannelExitPublisherSource
    from src.trading_runtime.native_channel_exit_journal_context import channel_contexts_by_batch
    source=getattr(publisher,'_native_channel_exit_source',None)
    if (type(policy) is not NativeChannelManagementPolicy
            or type(publisher) is not BacktestTypedJournalPublisher
            or type(source) is not NativeChannelExitPublisherSource
            or source.policy != policy or source.run_id != run_id
            or reader.execute("SELECT getSetting('readonly')").strip() != '1'):
        raise ValueError('Native inherited history requires its exact read-only publisher source')
    history=source.contexts
    channel_contexts_by_batch(run_id,history,max_commits=source.max_commits)
    result=_NativeHistoryReader(reader,publisher,source,history)
    result._require()
    return result


@dataclass(frozen=True, slots=True)
class NativeInheritedCheckpointRows:
    """Keep the real v5 root alongside its fully verified inherited image."""
    native_rows: object
    policy: NativeChannelManagementPolicy

    def require(self):
        from src.trading_runtime.native_channel_manager_snapshot import verify_native_channel_manager_rows
        rows=verify_native_channel_manager_rows(self.native_rows,self.policy)
        if rows.pending or rows.sources or rows.executions:
            raise ValueError('Inherited checkpoint cannot ignore pending native exits')
        return rows

    @property
    def snapshot(self):
        return self.require().snapshot


def load_unattested_native_inherited_checkpoint_rows(client, *, run_id,
        checkpoint_sequence):
    from src.trading_runtime.native_channel_manager_reader import load_unattested_native_channel_manager_rows
    policy=getattr(client,'native_channel_management_policy',None)
    if type(policy) is not NativeChannelManagementPolicy:
        raise ValueError('Native inherited rows require the declared native policy')
    rows=load_unattested_native_channel_manager_rows(client,run_id=run_id,
        checkpoint_sequence=checkpoint_sequence,policy=policy)
    result=NativeInheritedCheckpointRows(rows,policy)
    result.require()
    return result


@dataclass(frozen=True, slots=True)
class NativeInheritedCheckpoint:
    rows: object
    inherited: object


def load_current_native_inherited_checkpoint(client, keeper, *, run_id,
        checkpoint_sequence, policy, first_price_source,
        native_channel_contexts=()):
    """Use the real selected v5 root, retaining all existing attestation.

    Inherited management precedes native exit staging. Its checkpoint must
    therefore have no native pending decisions. Empty independent evidence
    fails closed on a nonempty saved inventory rather than dropping it.
    Complete prior native journal contexts are still required by the reader.
    """
    if (type(policy) is not NativeChannelManagementPolicy
            or getattr(client, 'native_channel_management_policy', None) != policy):
        raise ValueError('Inherited native checkpoint requires its declared reader policy')
    rows, state, requests = load_attested_native_channel_manager_snapshot(
        client, keeper, run_id=run_id, checkpoint_sequence=checkpoint_sequence,
        policy=policy, contexts={}, first_price_source=first_price_source,
        native_channel_contexts=native_channel_contexts)
    if requests or rows.pending or rows.sources or rows.executions:
        raise ValueError('Inherited checkpoint cannot ignore pending native exits')
    return NativeInheritedCheckpoint(rows, state)
