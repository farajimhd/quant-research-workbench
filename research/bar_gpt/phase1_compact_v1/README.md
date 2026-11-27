# Phase 1 compact release V1

The migration reads the corrected balanced cohort, its sealed chronological
split, and both certified loader indices. It preserves original shard order,
split membership, and all 64-bar sequences including source rows purged from
the usable split. A pool shares bars only when the timestamps, all Float32
values, and all validity bytes are identical. Equal timestamps on distinct
split-adjusted bases retain distinct pool entries. A contiguous window uses a
single pool offset and irregular windows use explicit locators.

Each original source payload is SHA-verified on read. Each compact part is
written, read back from disk, and reconstructed against the original record
bytes before its completion is committed to the catalog. The catalog identifies
source payloads and split ordinals and is sealed at completion. Source-relative
keys and numeric compact part names keep the format portable. Existing Phase 1
shards remain the training authority until a separate loader cutover is tested.

On the workstation, with the hash-verified source snapshot as the working directory:

    python -B -m research.bar_gpt.phase1_compact_v1.run --cohort <corrected-cohort> --output <new-sibling-runtime> --source-revision <revision> --workers 32
    python -B -m research.bar_gpt.phase1_compact_v1.panel --run <new-sibling-runtime>

A STOP file prevents additional batches. Resume the same job with the original
command after the operator clears that marker. A separate pilot output with
`--pilot N` never writes a production certificate. A completed release can be
read on a different drive with `CompactRelease(new_root)`. Its `read(split,
ordinal)` returns the corresponding original loader slice.
