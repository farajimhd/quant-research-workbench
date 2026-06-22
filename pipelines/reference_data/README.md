# Reference Data Pipeline

This package owns reference-data loads and q_live migration scripts.

## Market References

Use this loader for dense market reference tables:

```powershell
python -m pipelines.reference_data.run_load_market_references
```

## q_live Migration

The historical q_live migration scripts live in:

```text
pipelines/reference_data/migration/
```

Run migration steps by module path, for example:

```powershell
python -m pipelines.reference_data.migration.step_01_create_q_live_schema --help
```

## Ongoing Reference Gateway

Slow-changing identity/reference sync is owned by:

```text
services/reference_gateway/
```

The first executable step is a read-only audit/planner:

```powershell
python -m services.reference_gateway.main
```

It enforces the rule that any unresolved identity, exchange, conid, or mapping
issue keeps the affected security out of the tradable universe.
