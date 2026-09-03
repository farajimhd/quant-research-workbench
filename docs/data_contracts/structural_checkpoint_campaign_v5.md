# Structural Checkpoint Campaign v5

## Authority

Campaign v5 retains the v4 ticker-sharded, exact-ordinal execution model and
Generic Structure algorithm v16. It adds mandatory checkpoint certification.
The same certification code is compiled into QMD Live and QMD History; neither
historical nor live daily checkpoint persistence may write an uncertified row.

The certification is evidence about deterministic source coverage and state
transition integrity. It does not claim that a trading strategy is profitable
or that algorithm v16's market interpretation has been independently approved.

## Per-checkpoint certification

Every daily checkpoint row stores `certification_json` with:

- a SHA-256 digest of every canonical compact event in causal replay order;
- event count, first and last arrival sequence, and first and last SIP time;
- explicit contiguous-ordinal evidence for archive campaign sessions;
- a canonical SHA-256 of the complete Generic Structure checkpoint;
- a SHA-256 of the checkpoint's complete applied-split lineage;
- predecessor checkpoint and predecessor chain hashes; and
- a chain hash binding ticker, session, authority start, algorithm version,
  source-plan hash, source-revision token, event evidence, split lineage,
  predecessor identity, and resulting checkpoint.

Canonical checkpoint hashing recursively sorts JSON object keys. Hash identity
therefore does not depend on Rust `HashMap` iteration order, worker assignment,
chunk size, or JSON object insertion order. Floating-point values retain their
serialized value; no rounding is introduced for certification.

## Fail-closed behavior

The archive campaign rejects a duplicate, missing, or out-of-order ordinal
before persistence. It also verifies exact session count, ordinal endpoints,
SIP endpoints, ticker, date, source completeness, and source revision.

The shared ClickHouse writer recomputes checkpoint, split, and chain hashes and
refuses missing, unsupported, incomplete-chain, or mismatched certification.
`certification_json` participates in the insert deduplication token, so a
certified replacement of a legacy row cannot be mistaken for its older
uncertified write.

The checkpoint-set registry records certification schema and certified row
count. Sealing fails unless every latest logical checkpoint row is certified.
Counts use `FINAL`, so replaced legacy rows do not inflate coverage while
background merges are pending.

## Resume and legacy checkpoints

Certified checkpoints remain restart-safe and are used as resume seeds only
when source identity and every certification hash validate. An uncertified v4
checkpoint is preserved but is not trusted as a v5 seed. The worker replays
that ticker from the campaign authority start, writes certified replacement
rows, and subsequently resumes from the latest certified checkpoint. No table
purge is required.

## Live service

The live daily-checkpoint endpoint consumes QMD History replay evidence from
the exact source pass used to produce the checkpoint. It builds the same
checkpoint, split, predecessor, and chain hashes and writes through the same
fail-closed persistence authority. Existing uncertified live rows are not
returned as current or used as seeds; the requested ticker is rebuilt from its
configured authority before a certified row is published.

Current live checkpoint creation remains driven by the existing daily
checkpoint workflow. Certification does not introduce a second scheduler or a
second level-book calculation.
