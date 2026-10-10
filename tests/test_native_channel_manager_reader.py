import re

import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_manager_recovery import manager_case
from tests.test_original_risk_profile import WAIT
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.native_channel_manager_reader import load_unattested_native_channel_manager_rows
from src.trading_runtime import strategy_one_management_snapshot as manager
from src.trading_runtime.arte_native_channel_pending_schema import TABLES
from src.trading_runtime.original_risk_pending_snapshot import PENDING as ORIGINAL_PENDING
from src.trading_runtime.arte_journal_writer import verify_typed_contract_row
from src.backend.backtest_native_channel_manager_recovery import reprove_native_channel_manager_snapshot


class Reader:
    def __init__(self, rows, policy, defect=None):
        self.confirmed_original_risk_policy = WAIT
        self.entry_spread_risk_profile = False
        self.native_channel_management_policy = policy
        inherited, protection = rows.inherited, rows.inherited.protection
        self.rows = {
            'trading_strategy_one_manager_snapshot_v5': (rows.snapshot,),
            'trading_strategy_one_protection_snapshot_v1': (protection.snapshot,),
            'trading_strategy_one_protection_state_v1': protection.states,
            'trading_strategy_one_protection_resistance_v1': protection.resistances,
            manager.SOURCE.name: inherited.sources, manager.BREAK.name: inherited.pending_breaks,
            manager.HIGH.name: inherited.position_highs, manager.CLOSED.name: inherited.closed_positions,
            manager.FIRST_HELD.name: inherited.first_held_boundaries,
            ORIGINAL_PENDING.name: inherited.original_risk_requests,
            **{t.name: tuple(verify_typed_contract_row(t, row) for row in values)
               for t, values in zip(TABLES, (rows.pending, rows.sources, rows.executions))},
        }
        self.defect, self.sql = defect, []

    def execute(self, sql):
        assert sql.startswith('SELECT ')
        self.sql.append(sql)
        table = re.search(r'FROM arte\.([a-z0-9_]+)', sql)[1]
        rows = self.rows[table]
        if self.defect == 'duplicate_root' and table.endswith('snapshot_v5'): rows = rows * 2
        if table == TABLES[1].name:
            if self.defect == 'missing': rows = ()
            if self.defect == 'extra': rows = rows * 2
        return '\n'.join(canonical_json(row) for row in rows)


def test_cold_reader_loads_complete_versioned_image_without_legacy_root_selection(installed):
    rows, policy, contexts = manager_case(installed)
    client = Reader(rows, policy)
    loaded = load_unattested_native_channel_manager_rows(client, run_id=rows.snapshot['run_id'],
        checkpoint_sequence=rows.snapshot['checkpoint_sequence'], policy=policy)
    assert loaded.snapshot == rows.snapshot
    assert len(loaded.pending) == 1
    assert len(reprove_native_channel_manager_snapshot(loaded, policy, contexts)) == 1
    assert not any('FROM arte.trading_strategy_one_manager_snapshot_v4 ' in sql for sql in client.sql)
    assert all('LIMIT ' in sql for sql in client.sql)


@pytest.mark.parametrize('defect', ['duplicate_root', 'missing', 'extra'])
def test_cold_reader_rejects_ambiguous_or_incomplete_images(installed, defect):
    rows, policy, _ = manager_case(installed)
    with pytest.raises(ValueError):
        load_unattested_native_channel_manager_rows(Reader(rows, policy, defect),
            run_id=rows.snapshot['run_id'], checkpoint_sequence=rows.snapshot['checkpoint_sequence'], policy=policy)
