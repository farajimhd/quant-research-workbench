"""Uninstalled versioned manager root; no runtime imports or writer admission."""
from .arte_journal_schema import TableContract
from .original_risk_pending_snapshot import selected_parent_contract


def parent_contract():
    inherited = selected_parent_contract()
    return TableContract('trading_strategy_one_manager_snapshot_v5', (
        *inherited.columns[:-1],
        ('native_channel_pending_count', 'UInt32'), ('native_channel_pending_hash', 'FixedString(64)'),
        ('native_channel_source_count', 'UInt32'), ('native_channel_source_hash', 'FixedString(64)'),
        ('native_channel_execution_count', 'UInt32'), ('native_channel_execution_hash', 'FixedString(64)'),
        inherited.columns[-1]), inherited.partition, inherited.order)
