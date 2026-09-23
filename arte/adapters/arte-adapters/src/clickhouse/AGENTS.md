# ClickHouse persistence and recovery

Read `../../../../doc/03-events.md`, `../../../../doc/04-data-lifecycle.md`,
and `../../../../doc/05-v7-state.md` for the affected table.

- Use only the configured ARTE database. Operational tables require the
  explicit `live_market_ssd` policy and verified actual part placement; never
  fall back to `default` or an alias routing there.
- ClickHouse is the sole durable store. No SQLite, parent database, flatfile,
  local checkpoint, or cache may become a second authority.
- Define immutable identities, revisions, source/knowledge clocks, and exact
  read resolution. ClickHouse sorting and background merges do not enforce
  uniqueness; reject conflicting rows before and after insert.
- Publish source coverage only after source verification. Publish derived
  manifests or checkpoint roots last, after every referenced object has been
  inserted and read back. Incomplete generations remain invisible.
- Treat ambiguous inserts as retryable with the same identity. Never overwrite
  an old live observation with later REST knowledge. Preserve counts and
  reasons for rejected, deferred, retried, and failed units.
- Keep queries bounded by instrument, session, interval, and required columns.
  Verify schema compatibility, storage policy, and part placement before
  starting writers; an `IF NOT EXISTS` statement is not migration proof.
