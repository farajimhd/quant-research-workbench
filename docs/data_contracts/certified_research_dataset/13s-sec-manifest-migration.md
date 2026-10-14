# SEC acquisition manifest SSD migration

[Completion audit](13i-completion-audit.md)

`scripts/migrate_sec_acquisition_manifest_storage.py` reuses the existing
verified migration engine with a fixed three-table scope:

- `q_live.sec_filing_entity_archive_manifest_v3`
- `q_live.sec_filing_text_rendered_rebuild_manifest_v3`
- `q_live.sec_filing_text_rendered_rebuild_bundle_manifest_v3`

The default command is a read-only plan. Execution requires `--execute
--writers-stopped`, a maintenance declaration; active writes, mutations and
queued inserts are checked as well. Keep SEC writers stopped until the journal
reports completion. Keeper source ownership is checked before every modifying
command. The script does not stop external services automatically.

```powershell
python -B scripts/migrate_sec_acquisition_manifest_storage.py
```

## Preservation and restart contract

The migration creates an SSD replacement from the source schema, copies all
stored versions by partition, and verifies sorted SHA-256 row hashes including
duplicate multiplicity. Raw reads explicitly use `final=0`; copies also use
`optimize_on_insert=0`. Merely omitting `FINAL` is insufficient when server
settings can collapse revisions. Partition enumeration uses the same raw mode.

After whole-table reconciliation and actual SSD part checks, Atomic `EXCHANGE
TABLES` cuts over the original name. The original UUID is retained under the
journal-owned `_backup_...` name with its merges stopped. No original table is
dropped in this mode. New writes use the SSD table; a pre-existing default-disk
original remains a backup. The replacement's default compression is ZSTD(1).
Existing explicit column codecs and logical types remain intact.

A journal under `D:/TradingML/runtimes/sec-manifest-storage-migration-v1` binds
database, exact table scope, preservation mode, source/replacement UUIDs,
partition digests and completion. It contains operational metadata, not exported
source rows. Source data stays inside ClickHouse. The runtime root must exist;
there is no alternate-location fallback. Interrupted copies reconcile existing
owned staging partitions; interrupted exchanges are detected by UUID, preventing
an exchange back. Completed resume checks current policy/parts and backup UUID.

The capacity check requires twice the unfinished source physical bytes plus a
configurable reserve (default 256 GiB). This is a bounded migration allowance,
not a qualified market/reference product projection. No schema move, retention
change or compression claim can substitute for the content/part checks.

## Validation and operational status

Eight unit/regression tests passed, plus one real isolated ClickHouse migration
test: duplicate versions across two monthly partitions survived copying, cutover
retained the original backup, and completed resume kept the new UUID. The legacy
structural migration defaults and retention behavior remain unchanged.

A production read-only plan identified the three existing default-policy
manifests. No production exchange, copy, migration or acquisition was executed.
The real fixture used an SSD source because tests must not create operational
tables on the backup disk; production default-to-SSD execution remains an
operational acceptance step under the stated maintenance conditions.
