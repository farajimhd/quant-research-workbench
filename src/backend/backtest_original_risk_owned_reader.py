"""Publisher-bound SELECT-only checkpoint reader; never a writer grant."""
from weakref import WeakKeyDictionary
from threading import RLock

from src.trading_runtime.journal_contract import canonical_json

_ISSUED = WeakKeyDictionary()
_LOCK = RLock()


def _binding(proxy):
    with _LOCK:
        binding = _ISSUED.get(proxy)
    if binding is None:
        raise ValueError('Checkpoint reader has no issued publisher binding')
    reader, manager, publisher, writer, owner_client, source, contract, config, policy = binding
    from src.trading_runtime.original_risk_verification_reuse import selected_policy
    if (manager.contract is not contract or publisher.writer is not writer
            or writer._client is not owner_client or publisher._first_price_source is not source
            or publisher.journal.run_id != source.run_id
            or canonical_json(publisher.expected_config) != config
            or selected_policy(reader, source) != policy
            or selected_policy(owner_client, source) != policy):
        raise ValueError('Checkpoint reader publisher/source/contract binding changed')
    return binding


class _OwnedCheckpointReader:
    def __getattr__(self, name):
        return getattr(_binding(self)[0], name)

    @property
    def backtest_v4_lease(self):
        return _binding(self)[4].backtest_v4_lease

    @property
    def typed_insert_dispatch(self):
        return _binding(self)[4].typed_insert_dispatch

    @property
    def typed_insert_strict(self):
        return _binding(self)[4].typed_insert_strict

    def execute(self, sql, *args, **kwargs):
        if type(sql) is not str or args or kwargs:
            raise ValueError('Owned checkpoint reader accepts exact SELECT text only')
        from scripts.clickhouse.report_strategy_one_trades import report_select_query
        return _binding(self)[0].execute(report_select_query(sql))


def bind_original_risk_checkpoint_reader(reader, *, manager, publisher, run_id):
    """Issue only from the installed declaration and exact original publisher.

Ordinary cold readers receive no binding. This facade routes all SQL through
the separately preflighted read-only client; owner references only fence the
request-local verification engine and cannot execute INSERTs.
"""
    contract = manager.contract
    policy = getattr(contract, 'original_risk_verification_reuse_policy', None)
    if policy is None:
        return reader
    from .backtest_strategy_one_management import StrategyOneManagementRunner
    from .backtest_typed_publisher import BacktestTypedJournalPublisher
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    from src.trading_runtime.original_risk_verification_reuse import (
        selected_policy, exclusive_writer_frontier_fence)
    if (type(manager) is not StrategyOneManagementRunner
            or type(publisher) is not BacktestTypedJournalPublisher
            or contract != numbered_fixed_strategy(contract.strategy_number)):
        raise ValueError('Checkpoint reader requires exact installed native owners')
    source = publisher._first_price_source
    writer = publisher.writer
    owner_client = writer._client
    config = publisher.expected_config
    if (publisher.journal.run_id != run_id or source.run_id != run_id
            or writer.journal_profile != 'backtest_v4'
            or type(config) is not dict
            or config.get('strategy_revision') != contract.strategy_number
            or config.get('strategy_id') != contract.strategy_id
            or selected_policy(reader, source) != policy
            or selected_policy(owner_client, source) != policy
            or reader.execute("SELECT getSetting('readonly')").strip() != '1'):
        raise ValueError('Checkpoint reader differs from publisher run/source/profile')
    if exclusive_writer_frontier_fence(owner_client, source, allow_unavailable=True) is None:
        return reader
    encoded = canonical_json(config)
    if len(encoded.encode()) > policy.max_bytes:
        return reader
    proxy = _OwnedCheckpointReader()
    with _LOCK:
        if len(_ISSUED) >= policy.max_requests:
            return reader
        _ISSUED[proxy] = (reader, manager, publisher, writer, owner_client, source,
                          contract, encoded, policy)
    _binding(proxy)
    return proxy
