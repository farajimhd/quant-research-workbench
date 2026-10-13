"""One operator-owned inventory for the current programme app storage path."""
from src.trading_runtime.arte_journal_schema import TableContract


def program_app_storage_contracts():
    from src.trading_runtime.rule_decision_publication import rule_decision_storage_contracts
    from src.trading_runtime.columnar_strategy_release import TABLES as RELEASE_TABLES
    from src.trading_runtime.strategy_configuration_schema import TABLES as CONFIGURATION_TABLES
    from src.trading_runtime.strategy_signal_configuration_schema import TABLES as CONFIGURED_SIGNAL_TABLES
    from src.market_engine.columnar_history_product import TABLES as HISTORY_TABLES
    from src.market_engine.opening_price_reference import TABLES as OPENING_TABLES
    from .signal_stream_typed_cursor import TABLES as CURSOR_TABLES
    from .signal_stream_typed_occurrence import TABLES as OCCURRENCE_TABLES
    from .signal_dispatch_typed_cursor import DISPATCH_TABLES
    tables = (*rule_decision_storage_contracts(), *RELEASE_TABLES, *CONFIGURATION_TABLES,
        *CONFIGURED_SIGNAL_TABLES, *HISTORY_TABLES, *OPENING_TABLES)
    # These source contracts have one declared session-key partition. Adapt
    # their metadata to the same shared physical/schema auditor, without
    # adding a second schema or changing their existing scalar fields.
    source = tuple(TableContract(table.name, table.columns, 'toYYYYMM(session_key)', table.order)
        for table in (*CURSOR_TABLES, *OCCURRENCE_TABLES, *DISPATCH_TABLES))
    contracts = {}
    for table in (*tables, *source):
        prior = contracts.setdefault((table.database, table.name), table)
        if prior != table:
            raise ValueError('Programme app storage has conflicting central contracts: ' + table.name)
    return tuple(contracts.values())
