"""Selected manager seal draft; ordinary manager schema remains unchanged.

This sole contract binds the full active own protection inventory into the
existing complete manager capture. Definition alone grants no write authority.
"""
from .arte_journal_schema import TableContract
from .strategy_one_management_snapshot import PARENT_V3

PARENT=TableContract('trading_fixed_structural_lot_manager_snapshot_v1',
    (*PARENT_V3.columns[:-1],
     ('selected_configuration_hash','FixedString(64)'),
     ('selected_position_count','UInt32'),
     ('selected_position_hash','FixedString(64)'),
     PARENT_V3.columns[-1]),PARENT_V3.partition,PARENT_V3.order)
TABLES=(PARENT,)
