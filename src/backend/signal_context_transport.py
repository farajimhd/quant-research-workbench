"""Run-scoped physical transport reusing the shared logical source codecs."""
from __future__ import annotations
import json
from .signal_execution_context import SignalExecutionContext, restore_context_row, restore_context_seal, seal_context
from .signal_context_schema import CONTEXT, context_table
from .signal_stream_typed_readback import canonical_row
from .signal_source_insert_dispatch import SignalSourceInsertDispatch
from .signal_stream_session_head import SignalSessionHeadKeeper
from src.trading_runtime.journal_contract import canonical_json


def require_context_components(context, *components):
    if type(context) is not SignalExecutionContext or any(getattr(item, "source_context", None) != context for item in components):
        raise ValueError("Signal transport lacks its exact native execution context")


def read_context_rows(client, context, logical_table, predicate, maximum):
    if type(context) is not SignalExecutionContext:
        raise TypeError("Contextual read requires a concrete execution context")
    if type(maximum) is not int or not 1 <= maximum <= 100_000:
        raise ValueError("Contextual read requires a finite declared row bound")
    physical = context_table(logical_table)
    columns = ",".join(key for key, _ in physical.columns)
    sql = (f"SELECT {columns} FROM arte.{physical.name} WHERE source_context_id='{context.context_id}' AND ({predicate}) LIMIT {maximum + 1} FORMAT JSONEachRow")
    raw = [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]
    if len(raw) > maximum:
        raise RuntimeError("Contextual family exceeds its explicit read bound")
    result = []
    keys = {key for key, _ in logical_table.columns}
    for row in raw:
        normalized = canonical_row(logical_table, {key: value for key, value in row.items() if key in keys})
        envelope = {**row, **normalized}
        result.append(restore_context_row(context, logical_table, envelope))
    return result


def verify_context_seal(client, context):
    if type(context) is not SignalExecutionContext:
        raise TypeError("Context seal read requires concrete native identity")
    columns = ",".join(key for key, _ in CONTEXT.columns)
    sql = f"SELECT {columns} FROM arte.{CONTEXT.name} WHERE source_context_id='{context.context_id}' LIMIT 2 FORMAT JSONEachRow"
    rows = [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]
    if len(rows) != 1 or restore_context_seal(rows[0]) != context:
        raise ValueError("Native signal execution context seal is absent or duplicated")


class ContextSignalSourceInsertDispatch(SignalSourceInsertDispatch):
    def __init__(self, keeper, *, source_context):
        if type(source_context) is not SignalExecutionContext:
            raise TypeError("Source dispatch requires native execution context")
        super().__init__(keeper)
        self.source_context = source_context
        self.run_id = f"signal-source:{source_context.session_key}:{source_context.context_id}"
        from .signal_source_insert_dispatch import _CONTEXT_TABLES
        self._tables = _CONTEXT_TABLES

    def _scope(self, run_id):
        if run_id != self.run_id:
            raise ValueError("Source INSERT gate crossed execution contexts")


class ContextSignalSessionHeadKeeper(SignalSessionHeadKeeper):
    def __init__(self, client, *, endpoint, source_context):
        if type(source_context) is not SignalExecutionContext:
            raise TypeError("Source head requires native execution context")
        super().__init__(client, endpoint=endpoint)
        self.source_context = source_context

    def _base(self, session_key):
        if session_key != self.source_context.session_key:
            raise ValueError("Source head calendar differs from execution context")
        return f"/trading/signal-stream-session/v2/{self.source_context.context_id}"


from .signal_dispatch_insert_dispatch import SignalDispatchInsertDispatch

class ContextSignalDispatchInsertDispatch(SignalDispatchInsertDispatch):
    def __init__(self, keeper, *, source_context):
        if type(source_context) is not SignalExecutionContext:
            raise TypeError("Dispatch requires native execution context")
        super().__init__(keeper)
        self.source_context = source_context
        self.run_id = f"dispatch:{source_context.session_key}:{source_context.context_id}"
        from .signal_dispatch_insert_dispatch import _TABLES
        self._tables = tuple(tuple(context_table(name).name for name in pair) for pair in _TABLES)

    def _scope(self, run_id):
        if run_id != self.run_id:
            raise ValueError("Dispatch gate crossed native execution contexts")

    def _commit_family_hash(self, commit_hash, *, phase):
        from .signal_dispatch_typed_cursor import INTENT_COMMIT, ACK_COMMIT
        from .signal_dispatch_insert_dispatch import dispatch_family_hash
        table = INTENT_COMMIT if phase == "intent" else ACK_COMMIT
        return dispatch_family_hash((context_logical_hash(self.source_context, table, commit_hash),))

    def physical(self, logical_name):
        from .signal_dispatch_typed_cursor import DISPATCH_TABLES
        if logical_name not in {table.name for table in DISPATCH_TABLES}:
            raise ValueError("Context dispatch table is unregistered")
        return context_table(logical_name).name


def context_logical_hash(context, table, content_hash):
    """Bind a verified logical scalar-row digest to the actual run and family."""
    from hashlib import sha256
    return sha256(canonical_json(dict(source_context_id=context.context_id, logical_table=table.name, content_hash=content_hash)).encode()).hexdigest()


def publish_context_seal(insert_client, read_client, keeper, context):
    """Seal once; an uncertain INSERT remains blocked pending reconciliation."""
    if type(context) is not SignalExecutionContext:
        raise TypeError("Context publication requires concrete native identity")
    from src.trading_runtime.keeper_ownership import _path, KeeperUnavailable
    from src.trading_runtime.arte_journal_schema import TableContract
    from src.market_engine.typed_product_insert import insert_product_rows
    root = _path("signal_execution_context_publication_v2")
    path = _path("signal_execution_context_publication_v2", context.context_id)
    pending = ("pending:" + context.seal_hash).encode()
    sealed = ("sealed:" + context.seal_hash).encode()
    keeper.ensure_path(root)
    try:
        keeper.create(path, pending)
    except Exception as exc:
        if type(exc).__name__ != "NodeExistsError":
            raise
        observed, _ = keeper.get(path)
        if observed != sealed:
            raise KeeperUnavailable("Context seal publication is unresolved; reconcile before retry") from exc
        verify_context_seal(read_client, context)
        return
    table = TableContract(CONTEXT.name, CONTEXT.columns, 'toYYYYMM(session_key)', CONTEXT.order)
    insert_product_rows(insert_client, table, [seal_context(context)])
    verify_context_seal(read_client, context)
    observed, stat = keeper.get(path)
    if observed != pending:
        raise KeeperUnavailable("Context publication gate changed before readback")
    keeper.set(path, sealed, version=stat.version)
