# Database source

Future schema and migration definitions target only the new configured ClickHouse
database. Event identity and revision gates must pass before final DDL is introduced.

Use explicit workload storage policies and verify actual part placement. Never alter
legacy tables. Keep database contents and generated migration reports outside source.
No migration is implemented or authorized to run by this scaffold.
