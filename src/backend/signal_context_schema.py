"""Opt-in contextual Signal Stream tables; legacy contracts stay unchanged."""
import re

from .signal_stream_typed_cursor import TypedTable, TABLES as CURSOR_TABLES
from .signal_stream_typed_occurrence import TABLES as OCCURRENCE_TABLES
from .signal_dispatch_typed_cursor import DISPATCH_TABLES


CONTEXT = TypedTable('signal_execution_context_v2', (
    ('schema_version', 'UInt16'), ('session_key', 'Date'),
    ('source_context_id', 'FixedString(64)'), ('run_id', 'String'),
    ('source_journal_run_id', 'String'), ('mode', 'LowCardinality(String)'),
    ('configuration_revision', 'String'), ('source_revision', 'String'),
    ('content_hash', 'FixedString(64)')), 'source_context_id, session_key')
LOGICAL_TABLES = (*CURSOR_TABLES, *OCCURRENCE_TABLES, *DISPATCH_TABLES)
_MAP = {table.name: TypedTable(table.name.removesuffix('_v1') + '_context_v2',
    table.columns + (('source_context_id', 'FixedString(64)'),
                     ('context_content_hash', 'FixedString(64)')),
    'source_context_id, ' + table.order) for table in LOGICAL_TABLES}
TABLES = (CONTEXT, *_MAP.values())


def context_table(logical_table):
    name = logical_table if type(logical_table) is str else getattr(logical_table, 'name', None)
    if name not in _MAP:
        raise ValueError('Unknown logical Signal Stream table')
    return _MAP[name]


def context_table_map():
    return dict(_MAP)


def schema_plan(*, reader_principals, publisher_principal):
    readers = tuple(reader_principals)
    if (len(set(readers)) != len(readers) or not readers
            or any(type(p) is not str or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', p) is None
                   for p in (*readers, publisher_principal))):
        raise ValueError('Context grants require explicit unique principal names')
    grants = tuple(f'GRANT SELECT ON arte.{table.name} TO {principal}'
                   for principal in dict.fromkeys((*readers, publisher_principal)) for table in TABLES)
    grants += tuple(f'GRANT INSERT ON arte.{table.name} TO {publisher_principal}' for table in TABLES)
    return {'tables': TABLES, 'ddl': tuple(table.ddl() for table in TABLES), 'grants': grants}
