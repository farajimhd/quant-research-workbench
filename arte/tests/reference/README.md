# Frozen reference sources

These text snapshots pin the latest available strategy and V7 source at the start
of the Rust port. The user approved using the latest source as a starting point.
`origin.json` records each source path, commit, and SHA-256 before copying.
Git preserves these snapshots as opaque bytes. This retains original line endings
and hashes across extraction. Rust and other editable source retain normal text checks.

The snapshots are not executable Python packages. They are not imported by production ARTE,
included in its production release, or read from the parent repository at runtime.
They preserve the complete original text, including historical imports, for review.

The source profile builder identifies `v7-setup-recovery-v9`. The effective persisted
application configuration has not been read from a running service. It must not be
represented as pinned by these source snapshots alone.

The offline `check_extraction_parity.py` test verifies the frozen extractor's hash,
then evaluates that snapshot in an isolated module with NumPy and SciPy. This is
test-only execution, not a production Python dependency. It compares 70 deterministic
cases with the Rust example executable. Generated build output stays outside source.
Use `scripts/validate.ps1 -RuntimeRoot <external-directory> -PythonExecutable <python-path>`.
Test dependencies are pinned in `requirements.txt`. The source loader verifies installed
versions against those pins before executing an oracle. The validator does not install them.

The frozen fixed-noise extractor is not the MLE historical book required by the
streaming source. Passing its tests does not establish compatible daily seeds.
The MLE seed builder, fitter and streaming engine have partial Rust implementations.
The separate fit comparison covers 87 deterministic cases. These checks do not
establish all-session convergence, streaming parity or complete strategy parity.
