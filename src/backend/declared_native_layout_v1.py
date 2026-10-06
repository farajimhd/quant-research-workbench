"""Explicit operator migration for declared native contracts; no grants or rows.

Legacy layout profiles remain unchanged. Runtime principals never acquire DDL
authority through this module. Existing incompatible tables are never repaired
or overwritten implicitly.
"""
from dataclasses import dataclass
from src.trading_runtime.arte_declared_native_entry_schema import TABLES as ENTRY
from src.trading_runtime.arte_declared_native_management_schema import TABLES as MANAGEMENT
from src.trading_runtime.arte_running_financial_checkpoint_schema import TABLES as CHECKPOINT
from src.trading_runtime.arte_journal_schema import storage_preflight, STORAGE_POLICY
from src.trading_runtime.arte_journal_writer import _rows

CONTRACTS = (*ENTRY, *MANAGEMENT, *CHECKPOINT)
LAYOUT_VERSION = 'declared-native-backtest-layout@1'


@dataclass(frozen=True, slots=True)
class DeclaredNativeLayoutResult:
    version: str
    verified_existing: tuple[str, ...]
    missing: tuple[str, ...]
    created: tuple[str, ...]


def install_declared_native_layout(client, *, apply=False):
    """Verify all existing contracts first; apply only missing, exact table DDL."""
    if type(apply) is not bool:
        raise ValueError('Native layout apply must be an explicit boolean')
    policies = _rows(client, "SELECT disks FROM system.storage_policies "
                    "WHERE policy_name='live_market_ssd' FORMAT JSONEachRow")
    if policies != [{'disks': [STORAGE_POLICY]}]:
        raise RuntimeError('Native layout requires exact SSD-only storage policy')
    if client.execute("SELECT count() FROM system.databases WHERE name='arte'").strip() != '1':
        raise RuntimeError('Native layout requires existing arte database')
    names = ','.join("'"+t.name+"'" for t in CONTRACTS)
    inventory = _rows(client, "SELECT name FROM system.tables WHERE database='arte' "
                      f'AND name IN ({names}) FORMAT JSONEachRow')
    expected = {t.name for t in CONTRACTS}
    if (any(set(r) != {'name'} or r['name'] not in expected for r in inventory)
            or len({r['name'] for r in inventory}) != len(inventory)):
        raise RuntimeError('Native layout installed inventory is ambiguous')
    existing = {r['name'] for r in inventory}
    installed = tuple(t for t in CONTRACTS if t.name in existing)
    if installed:
        storage_preflight(client, tables=installed)
    missing = tuple(t for t in CONTRACTS if t.name not in existing)
    created = []
    if apply:
        for table in missing:
            client.execute(table.ddl())
            storage_preflight(client, tables=(table,))
            created.append(table.name)
        storage_preflight(client, tables=CONTRACTS)
    return DeclaredNativeLayoutResult(LAYOUT_VERSION, tuple(t.name for t in installed),
        tuple(t.name for t in missing), tuple(created))
