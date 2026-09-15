# Frozen reference sources

These text snapshots pin the latest available strategy and V7 source at the start
of the Rust port. The user approved using the latest source as a starting point.
`origin.json` records each source path, commit, and SHA-256 before copying.
Git preserves these snapshots as opaque bytes. This retains original line endings
and hashes across extraction. Rust and other editable source retain normal text checks.

The snapshots are not executable Python packages. They are not imported by ARTE,
included in its production release, or read from the parent repository at runtime.
They preserve the complete original text, including historical imports, for review.

The source profile builder identifies `v7-setup-recovery-v9`. The effective persisted
application configuration has not been read from a running service. It must not be
represented as pinned by these source snapshots alone.

Ported primitives currently include Student-t objective/gradient, vector association,
causal preceding-range tracking, and candle geometry. Full V7 fitting/extraction and
the complete strategy lifecycle are not yet ported or parity-certified.
