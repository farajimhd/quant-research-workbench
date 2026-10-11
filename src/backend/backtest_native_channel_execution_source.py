"""Current-held reproof for the shared runtime's declared native exit path."""
from dataclasses import dataclass

from .backtest_native_channel_publisher_source import NativeChannelExitPublisherSource
from .backtest_native_channel_exit_request import NativeChannelExitRequest
from .backtest_typed_publisher import TypedBacktestReceipt
from src.trading_runtime.native_channel_exit_journal_context import NativeChannelExitJournalContext


def prepare_native_channel_execution_source(source, receipt, capture, request, *,
        recorded_at, attempt_id, batch_id, parent_record_id, decision_record_id):
    """Cold-rebuild the full frozen inventory against its saved checkpoint.

    The caller still routes the returned request through runtime confirmation,
    Portfolio and OMS. This function performs no writes or order submission.
    """
    from .backtest_native_channel_management_owner import NativeChannelPendingCapture
    from .backtest_native_channel_exit_unit import prepare_confirmed_native_channel_exit_request
    from src.trading_runtime.native_channel_manager_reader import load_unattested_native_channel_manager_rows
    if (type(source) is not NativeChannelExitPublisherSource
            or type(receipt) is not TypedBacktestReceipt
            or type(capture) is not NativeChannelPendingCapture
            or type(request) is not NativeChannelExitRequest
            or capture.run_id != source.run_id or capture.policy != source.policy
            or sum(item.request is request for _, item in capture.items) != 1):
        raise ValueError('Native execution preparation requires its exact frozen pending inventory')
    capture.__post_init__()
    source.__post_init__()
    saved = load_unattested_native_channel_manager_rows(source.client,
        run_id=source.run_id, checkpoint_sequence=receipt.last_sequence, policy=source.policy)
    rebuilt, contexts = capture.capture(saved.inherited, journal_batch_id=receipt.last_batch_id)
    if rebuilt != saved:
        raise ValueError('Native execution frozen inventory differs from complete saved checkpoint')
    owner = (request.financial.account_id, request.financial.assignment_id, request.financial.ticker)
    keys = tuple(key for key, evidence in contexts.items()
        if (evidence['current_financial'].account_id, evidence['current_financial'].assignment_id,
            evidence['current_financial'].ticker) == owner)
    if len(keys) != 1:
        raise ValueError('Native execution requires exactly one pending position owner')
    unit, confirmed = prepare_confirmed_native_channel_exit_request(source.client,
        source.manager_keeper, source.broker_keeper, receipt, run_id=source.run_id,
        management_policy=source.policy, contexts=contexts, pending_record_id=keys[0],
        first_price_source=source.first_price_source, recorded_at=recorded_at,
        attempt_id=attempt_id, batch_id=batch_id, parent_record_id=parent_record_id,
        decision_record_id=decision_record_id, native_channel_contexts=source.contexts)
    context = NativeChannelExitJournalContext(unit, source.policy, contexts,
        source.first_price_source, recorded_at=recorded_at, attempt_id=attempt_id,
        batch_id=batch_id, parent_record_id=parent_record_id, decision_record_id=decision_record_id)
    execution = NativeChannelExitExecutionSource(source, receipt, context, keys[0])
    return confirmed, execution


@dataclass(frozen=True, slots=True)
class NativeChannelExitExecutionSource:
    """Read bindings, not order authority; confirm() independently reproves them."""
    publisher_source: NativeChannelExitPublisherSource
    receipt: TypedBacktestReceipt
    context: NativeChannelExitJournalContext
    pending_record_id: str

    def __post_init__(self):
        source, context, receipt = self.publisher_source, self.context, self.receipt
        if (type(source) is not NativeChannelExitPublisherSource
                or type(context) is not NativeChannelExitJournalContext
                or type(receipt) is not TypedBacktestReceipt
                or self.pending_record_id not in context.contexts
                or source.run_id != context.base.run_id or source.policy != context.policy
                or source.first_price_source is not context.first_price_source
                or context.base.prior_batch_id != receipt.last_batch_id
                or context.base.first_sequence != receipt.last_sequence + 1
                or context.base.source_cursor != receipt.source_cursor):
            raise ValueError('Native execution source crosses its exact pending checkpoint and publisher')
        source.__post_init__()
        context.__post_init__()

    def confirm(self, request):
        """Cold-reprove complete inventory and both current heads before OMS."""
        from .backtest_native_channel_exit_unit import prepare_confirmed_native_channel_exit_request
        self.__post_init__()
        source, context = self.publisher_source, self.context
        if type(request) is not NativeChannelExitRequest:
            raise ValueError('Native execution requires its exact typed request')
        unit, confirmed = prepare_confirmed_native_channel_exit_request(
            source.client, source.manager_keeper, source.broker_keeper, self.receipt,
            run_id=source.run_id, management_policy=source.policy,
            contexts=context.contexts, pending_record_id=self.pending_record_id,
            first_price_source=source.first_price_source, recorded_at=context.recorded_at,
            attempt_id=context.attempt_id, batch_id=context.batch_id,
            parent_record_id=context.parent_record_id, decision_record_id=context.decision_record_id,
            correlation_id=context.correlation_id, causation_id=context.causation_id,
            native_channel_contexts=source.contexts)
        if (unit.base != context.base or unit.sealed_families() != context.unit.sealed_families()
                or request.financial != confirmed.financial or request.acquisition != confirmed.acquisition
                or request.source_plan != confirmed.source_plan or request.policy != confirmed.policy
                or dict(request.decision) != dict(confirmed.decision)):
            raise ValueError('Native execution request differs from independently confirmed current ownership')
        return confirmed
