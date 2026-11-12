# Phase one: shard generator, data contract, quote repair and training record

## Explicit corrected-data continuation amendment (2026-11-10)

The user subsequently authorized stopping the original-data trainer, auditing
both completed quote-v4 members, and continuing from a complete checkpoint with
**40 additional epochs**, a fresh cosine schedule **3e-4 to 5e-5**, and no warmup.
Global epoch numbers continue from the parent; scheduler epoch zero is the first
corrected-data epoch. Model/decoder weights, AdamW moments, RNG and actual update
step are retained. The original `train-01` W&B identity is explicitly bridged.
Old-domain learning evidence remains separate; corrected-data fixed probes and
full-validation baseline run before any update. Patience eight and other guards
remain in force. `schedule_transition_v1` seals this narrow authorization; ordinary
resume compatibility stays strict. The preparation utility must receive
`reset_schedule=True`. A reporting offset preserves monotonic charts if a stopped
partial epoch uploaded beyond the retained checkpoint; `optimizer_global_step`
remains the true retained/replayed optimizer count. This amendment supersedes
older schedule-preservation notes below for this specific continuation only.
Activation and pilot/build results belong in native `corrected-restart-20261110`;
the existence of this amendment alone is not proof of activation.

Activation was subsequently verified in **train-06**, pinned to `19bc5c716`,
from immutable train-05 epoch 40 at actual optimizer step **1,817,587**. The
new total is epoch 80. All 884,750 repaired receipts passed the completion audit;
exact split membership/order/cutoff/purge and frozen calibration were retained.
Before updating, the corrected-data baseline measured untrained probe
0.7180275433, trained probe 0.08920595290 and full validation 0.10090916735.
These are new-domain baselines, not directly comparable to old-data losses.
Live metrics and remote W&B readback confirmed the original `train-01` /
`1rpdtas2` identity, `3e-4` learning rate and monotonic reported steps. A separate
`optimizer_global_step` retains the actual update count after partial-epoch replay.
The native old STOP markers and launcher were archived before owned-marker removal.

Recorded: 2026-11-10 UTC. Implementation and evidence were reviewed against the
laptop source and native runtime records. This is the consolidated phase-one
record; [PHASE2.md](PHASE2.md) defines the different phase-two storage contract.

## 1. Version names and authority

These version labels describe different things and must not be interchanged:

| Label | Meaning |
| --- | --- |
| `research/bar_gpt/v5` | Current implementation directory, including the phase-one MAE |
| `bargpt-ordinal-balanced-64-v6-no-halts` | Current 18-channel phase-one sample schema |
| Original `shards-2019-2025-v6` roots | Immutable originally generated training data |
| `-quote-v4` | Current source-first selective repair output algorithm |
| `bargpt-paired-quote-quarantine-v3` | Final quote-quality policy used by that repair |
| Historical BarGPT v3 | Earlier autoregressive model/extractor; not the current MAE or quote policy |

Laptop source authority is `D:/TradingCodes/quant-research-workbench`. Native
execution authority is `D:/TradingML/runtimes/bar_gpt` on `DESKTOP-SAAI85T`.
From the laptop this is
`//DESKTOP-SAAI85T/Workstation-D/TradingML/runtimes/bar_gpt`.
Committed code snapshots live in native `D:/TradingML/codes/bargpt-shards-<revision>`.
Laptop code must be tested, committed and pushed before hash-verified native sync.
Runtime outputs, tests, receipts, models and logs do not belong in the repository.

Historical market events come exclusively from certified
`market_sip_compact.events_YYYY`. The ordinal authority is the frozen
`market_sip_compact.events_ticker_day_index` inventory. Never reopen historical
SIP flatfiles or use ClickHouse `file()` as a fallback. Required schema,
dictionary, action coverage or storage-placement gaps fail closed.

Source preflight checks canonical/index table policies, dictionary/schema
identity and event-part placement. Canonical SIP uses `sip_raw_ssd`; ClickHouse
`default`/`hdd` is not an operational fallback. Reads are bounded SELECTs; the
extractor does not change server-global limits or rewrite canonical events.

## 2. Current record versus live operations

At the **2026-11-10T00:17:25 UTC** documentation snapshot:

| Item | Recorded evidence |
| --- | --- |
| Source-first controller | Reports running, revision `d92884a6a`, active member `phase1-liquid500` |
| Repaired base | Complete: 567,835 / 567,835 receipts; sealed `repair-result.json` exists |
| Repaired supplement | Reports repairing: 150,795 / 316,915 receipts; no final repair release yet |
| Corrected cohort | `phase1-v6-cohort-quote-v4/cohort.json` was not published at the preceding metadata check |
| Training | `train-05` reports epoch 38 training, source `87d561aa5`, still original data |
| Learning review | Epoch 37: `continue`, reason `learning or insufficient persistent evidence` |
| Reported best original-data validation loss | `0.11169072997553475`; do not relabel this as repaired-data evidence |
| Scheduled monitoring | Paused by user instruction; creating these records does not resume it |

These are dated file reports, not a new OS-process or remote W&B health
certification. Counters will move. Current ownership must be rediscovered from
the native ledgers and verified against PID creation time and command.

The documentation snapshot is outside source at
`D:/TradingML/runtimes/bar_gpt/documentation-records/phase-records-20261110.json`
on the laptop. Its seal is
`b9f07a66c6590d53f5ddb7d2066b6507d69ee50592e397346f11be232f8cec7c`.
It records hashes of the exact small runtime files read for this document.
The released base's repair-result seal is
`d90e3094f68c10b8631d016cd663d514d6593eecfd0c2a720576fef034e56a00`,
bound to repair contract
`853429ea557737cbf96798472b2a4d8deefc235e9aab8f4399977e70b5d30e58`.
That base release is not a release of the still-incomplete combined cohort.

## 3. What a phase-one shard stores

The authoritative current schema is
[shards/phase1_contract.py](shards/phase1_contract.py), not the archived
21-channel dtype in [shards/contract.py](shards/contract.py).

Each record is one complete 64-observation context:

| Field | Stored shape and dtype |
| --- | --- |
| `values` | `[64,18]`, little-endian Float32, unnormalized market units |
| `valid` | `[64,18]`, UInt8 evidence masks, values 0 or 1 |
| `end_us` | `[64]`, Int64 actual UTC completed-bar closes in microseconds |
| `timeframe_us` | Int64 duration identity |
| `split` | UInt8; the training split manifest supplies actual membership |

The unaligned structured record occupies **6,281 bytes**, excluding NPY headers.
Current v6 packing requires 64 positive, strictly increasing clocks; it does not
promote right padding into real observations. Older 21-channel/padded formats
remain historical migration inputs, not the current generation contract.

The exact channel order is:

| Indices | Fields |
| --- | --- |
| 0-5 | `trade_open`, `trade_high`, `trade_low`, `trade_close`, `trade_volume`, `trade_count` |
| 6-11 | `bid_open`, `bid_high`, `bid_low`, `bid_close`, `bid_size`, `bid_count` |
| 12-17 | `ask_open`, `ask_high`, `ask_low`, `ask_close`, `ask_size`, `ask_count` |

Six frames are saved: **100ms, 1s, 10s, 1m, 1h and regular-session daily**.
They are observed trade-bearing bars, not a fabricated complete clock grid.
Trade OHLC, eligible volume and count must all have positive complete evidence.
Quote-only messages cannot create a model bar. Missing quotes retain explicit
masks; an unknown quote is not an observed zero-price quote.

No halt channels, labels, future targets, learned embeddings, decoder outputs,
scaled tensors or nine derived UTC features are saved in these shards.
Current balanced v6 contexts can cross sessions and preserve actual gaps. Do
not apply phase two's single-session **origin-block** restriction retroactively
to these immutable phase-one sample windows.

## 4. Generation and cohort construction

```mermaid
flowchart LR
  A[Canonical SIP and frozen rules] --> B[Sealed ordinal inventory]
  B --> C[Independent fine and coarse aggregation]
  C --> D[Observed sliding 64-bar contexts]
  D --> E[Common sample-end split basis and quote policy]
  E --> F[Quota-controlled NPY publications and receipts]
  F --> G[Base plus unique liquid supplement]
  G --> H[One chronological split and training-only calibration]
```

### Base generator

[shards/balanced_plan.py](shards/balanced_plan.py) freezes the full 2019-2025
inventory in 7 years x 16 hash buckets = **112 Parquet partitions**, with exact
ordinal spans, counts, schema/dictionary and corporate-action provenance.
The retained broad universe has **21,556 tickers**, not v3's 300-ticker cohort.
Liquidity is a retrospective sampling proxy: top-quartile ticker-year mean
canonical events/source-day. It is not a profitability or contemporaneous
scanner feature and is not an encoder input.

[shards/balanced_build.py](shards/balanced_build.py) owns chronological ticker
work and separately schedules:

1. Fine 100ms/1s/10s/1m work in frozen, variable ordinal chunks. Chunk size is
   derived from source density and bounded by configuration; the start has a
   seeded offset. Partial edge buckets are excluded by the shared SQL.
2. Independent bounded timestamp spans for 1h/daily, with a 63-bar rolling tail
   so complete 64-bar contexts can cross source-span/session boundaries.

[shards/range_sql.py](shards/range_sql.py) and
[bar_semantics_v1.py](bar_semantics_v1.py) are the bar mathematics authority.
Raw messages stay in ClickHouse; typed Arrow returns final aggregates.
[shards/origin_samples.py](shards/origin_samples.py) uses vectorized array
packing: about **20% of eligible fine-frame sliding origins**, and all eligible
hourly/daily sliding origins, subject to saved-sample quotas and source coverage.
It applies the shared final quote policy and executed-action adjustment.

The base targets 75% liquid / 25% less-liquid **saved samples per timeframe**.
Its default daily allocation is 3%; the other five frame caps are equal.
Two percent of the nominal budget is reserved for publication/plan overhead.
Source exhaustion is explicit; samples are not duplicated to manufacture quota.

The recorded original base configuration used a nominal 1,000,000,000,000-byte
budget, 64 extraction threads, 16 I/O threads, 500,000-event chunks/cap,
20-source-day coarse spans, 1 GiB query memory and 32 MiB payload parts.
These are the **recorded run settings**, not all current CLI defaults. Always
read the immutable root's `contract.json` before reproducing a run.

### Liquid supplement

[liquid_universe_v1.py](liquid_universe_v1.py),
[supplement_v1.py](supplement_v1.py) and
[run_liquid_supplement.py](run_liquid_supplement.py) build an independent sibling.
The legacy root/member label is `liquid500`, but the recorded request was top
**512** activity-ranked names plus eligible pinned coverage names; the sealed
plan actually contains **557 ticker jobs**. Do not infer membership from a name.

Only originally liquid ending ticker-years are published. Full historical rows
remain attached for causal context. The supplement anti-joins the base by exact
ticker/frame first-and-last window clocks, verifying overlapping parent files.
It schedules coarse/fine work together, reserves scarce coarse capacity, gives
initial fine allowances fairly, then redistributes unused allowances. Its
nominal extra budget is 500,000,000,000 bytes.

The supplement is liquid-focused. **The combined phase-one cohort is not
automatically a 75/25 dataset merely because the base used that quota.** Phase
two independently enforces its explicitly requested 75/25 selected-origin mix.

### Publication, inventory and split authority

[shards/storage.py](shards/storage.py) writes bounded `samples-0000.npy` parts,
fsyncs them, reads hashes and publishes `receipt.json` last. Sealed outputs are
immutable; resume validates identity and does not overwrite them. Current v6
dtype is inferred from the passed sample array; legacy helpers in the same file
do not redefine the current schema.

Original roots:

```text
shards-2019-2025-v6/
  contract.json
  plan/manifest.json and year-bucket Parquet partitions
  jobs/<year-jobkey>/<work-unit-frame>/samples-*.npy and receipt.json
shards-2019-2025-v6-liquid500-v2/
  contract.json
  supplement-plan.json
  plan/<linked ordinal partitions>
  jobs/...
phase1-v6-cohort/cohort.json
```

The supplement intentionally need not contain `plan/manifest.json`.
`quote_rebuild_v1.frozen_jobs` was fixed in **`d92884a6a`** to identify that
layout, verify the parent binding and exact `supplement-plan.json` digest, and
load its own sealed membership. It must not rediscover a different universe or
substitute the base manifest. Evidence:
`quote-source-first-setup/liquid500-inventory-incident-20261109/`.

## 5. Prices, actions, normalization and input time features

Executed splits/reverse splits use the frozen covered reference catalog:

```text
before execution: price *= split_from / split_to
                  volume and displayed quote size *= split_to / split_from
unchanged: counts, evidence masks, order and UTC clocks
```

Execution is resolved at **04:00 America/New_York on the execution date**, using
shared `shards.sql.clock`; DST is explicit. Every bar in a phase-one sample is
adjusted to that sample's ending basis. Future executions do not alter earlier
origins. Canonical raw prices remain unchanged. All twelve price columns and
the three volume/size columns participate, including quotes.

[pretraining_v1.py](pretraining_v1.py) performs model preparation:

- Prices: `100 * log(price / reference)`, where the reference is the median of
  **visible, observed close values** in that window, not a hidden target price.
- Activity columns `[4,5,10,11,16,17]`: `(log1p(x)-median)/scale` using frozen
  training-only medians and IQRs. Bid/ask sizes share statistics; bid/ask counts
  share statistics. Zero IQR uses 1; missing activity-family evidence fails.
- There are two six-element vectors, `median[6]` and `scale[6]`, not six
  independently refitted timeframe normalizers.
- Missing source evidence stays masked and zero-filled; it is separate from
  artificial reconstruction masking.

[transforms_v1.py](transforms_v1.py) appends nine deterministic UTC features:
`bar_index`, `log_timeframe_seconds`, intraday sine/cosine, `log_gap_ratio`,
weekday sine/cosine and ISO-week sine/cosine. It preserves actual gaps; the first
gap has no predecessor and is masked. No absolute year, ticker or absolute
timestamp number is a model input. The combined feature contract is 18 market
plus 9 time channels; these time values are not stored in shard payloads.

## 6. Quote-source fixes and final quality policy

### Event eligibility

[quote_quality_v1.py](quote_quality_v1.py) is the executable policy authority.
The frozen canonical token dictionary, not a remembered vendor code number,
determines the mapping:

- Slots **1-4** are quote-condition tokens; slot **5** is a separate indicator
  family. Token 221, NBB/NBO Executable, must not be rejected as an unknown quote
  condition merely because it appears in slot 5.
- Token 48 maps to quote modifier 81, `Corrected_Price`; it is not a trade
  correction. Valid corrected, manual and slow quotes remain eligible unless
  another explicit rejection condition applies.
- Unknown nonzero quote-condition tokens fail closed. The rejected modifier
  list is `-1,15,18,19,20,32,34,35,36,37,38,39,43,80,83,84,89`.
- Prices and both displayed sizes must be finite and strictly positive; the
  paired book must be noncrossed. A locked book is not rejected just for equality.
- An actual invalid update **clears carry**. Dropping the update and reviving an
  older book is incorrect. Carry does not create an observed quote count.
- No arbitrary quote-age/freshness cutoff is added. A zero update count is not
  itself corruption when the available evidence is a carried book.

Trade price, extreme and volume eligibility remain separate shared predicates.
The updated encoder contract does not reject trades merely for delayed-reporting
status or missing participant timestamps. Quote fixes do not rewrite trades.

### Completed-bar quarantine: v3

`sanitize` checks available fields of a completed bar and masks **both** quote
families if any applicable reason fires. All twelve quote values and masks
become zero; trade fields, clocks and origin selection remain intact.

Reasons are nonfinite quote values; observed nonpositive prices/sizes;
negative or nonintegral observed counts; inconsistent OHLC geometry; crossed
paired close; paired opening or closing ask greater than ten times bid; or an
observed quote OHLC price more than tenfold outside the same completed bar's
valid trade low/high envelope. The OHLC open/close bound check uses the code's
`2e-6` relative tolerance. Count zero is allowed. Ratio comparisons use strict
`>`; the code uses multiplication to avoid unstable tiny-price division.

The guard is deterministic, idempotent and invariant to a common split factor.
It does not synthesize a replacement market price and is not a universal claim
that every wide positive market is erroneous or every plausible error is found.

Verified named cases included CPAC 2019-04-09, where the earlier 100-fold gate
was too loose; LARK 2020-05-08 ask 246.96 / bid 15 at the open; and AIRTW
2019-06-20 ask 1.21 / bid 0.05. Recovered closes did not undo contaminated opens,
so v3 added paired OPEN to the tenfold spread check. These are validated cases,
not proof of exhaustive anomaly removal.

## 7. Source-first selective repair

The active controller is `quote_repair_campaign_v1 --source-first --phase-one-only`.
Its logical flow is:

1. Read the original sealed inventory and nominate source issue/carry intervals
   with [quote_candidates_v1.py](quote_candidates_v1.py).
2. Map intervals to **full context envelopes**, not only sample origins, using
   [quote_selective_v1.py](quote_selective_v1.py). Missing bounds take full repair.
3. Decode candidate payloads, reconstruct quote evidence from canonical source
   through [quote_rebuild_v1.py](quote_rebuild_v1.py), and apply the final v3 guard.
4. Preserve trade/time/index/nonquote bytes and common-origin share adjustment.
5. Verify complete source SHA256 and independent target readback even when a
   payload avoided decoding; publish sealed immutable sibling receipts.
6. Reconcile source coverage plus exact decoded-mask deltas and the remaining
   work ledger before publishing a full repair release.

Nomination uses condition/size changes, wide books, carried-book/trade
dislocations and previous/next eligible extreme trades. Reverse-prefix windows
keep discovery linear. Future neighbors are **offline search hints only**,
never encoder inputs, final filtering rules or invented replacement prices.

Completed old v2 receipts can be reused only after exact original ancestry,
source/target hashes and policy checks. Old fixes are preserved. In reused
units, `changed_bars`/`quarantined` describe new pass edits; `changed_files`
relative to original can include inherited edits. Reason categories overlap.
Stored bar occurrences in overlapping windows, distinct clocks and unique SIP
messages are different quantities. Never sum old/new stages as unique bars.
Unchanged same-volume payloads may be hardlinked. Never mutate them with a
writable mmap or in-place edit: that would also alter the original. Changed
payloads require independently written/fsynced files.

Current targets are the two original member names with `-quote-v4` appended;
the eventual training cohort is **`phase1-v6-cohort-quote-v4`**, never v2/v3.
`require_complete` checks a sealed `repair-result.json` tied to its repair
contract. A pilot, readiness flag, or completed base alone is not the complete
two-member training handoff.

### Engineering, limits and measured improvements

- Repair uses **one unified 32-thread query/payload pool**, not phase two's
  separate CPU-process and I/O-thread pools. Payload-only Windows background
  priority is thread-local and restored on exit.
- `GroupedWork` admits one active receipt per job with bounded independent
  cursors. Profiling found 14/16 workers blocked on one ticker cache lock before
  this scheduling change; the lock was retained for integrity/coalescing.
- Repair queries use one ClickHouse thread, 60 seconds, initially 1 GiB. Exact
  same-SQL memory retries may use 2/4/8 GiB, at most two dense requests, preserving
  64 GiB plus requested-memory reserve and restoring client settings afterward.
- Keep at least 150 GiB disk reserve. Training-aware admission is four during
  validation/probes/startup/stale status and can reach 32 during training.
  The shared guard also releases full admission for controller `complete` or a
  fresh non-running child status; its explicit PID-creation inactivity test is
  in the early-stopped/stopped/failed terminal branch. Do not claim that every
  admission branch independently verifies OS ownership. Operational health and
  handoff checks must still verify creation time and command. The guard never
  signals the trainer; the controller holds its own keep-awake request.
- Quote-close batches increased 512 to 4,096, with SQLite `executemany` and
  vectorized fixed-record decoding after byte-layout validation.
- Recognized repair SELECTs get redundant UTC-month pruning only for ordinal
  spans <=250,000; larger scans keep the measured prior path. This is different
  from the phase-two calendar-month query-page policy.
- Exact unique ordered candidate prefixes use `anyLastIf` instead of carrying
  another `argMax`/`argMin` ordering tuple, with tie/empty/session parity checks.
- A complete, correctly bound zero-source-change candidate session may skip a
  redundant raw interval lookup. Missing/foreign/nonzero evidence falls back;
  the final quote guard still executes. This is not a new anomaly filter.

Controlled quote-close probes measured CBAY 3.93x and LARK 4.64x improvement from
batching. Adjacent mixed-work windows recorded about 2.073x for the first query
optimization, 2.342x for 16-to-32 workers, and 1.222x additional for ordered
candidate/clean-session work. These measurements overlap, use different work,
and **must not be multiplied or presented as corpus-wide speedup/ETA**.

### Validation and residual evidence

Before source-first cutover: 30 ticker/frame cases across all six frames;
14 real receipts/14 tickers covering 3,404 prior changed stored occurrences and
104 distinct changed clocks; zero tested misses and exact values/masks/trades/
clocks/split parity. An AAPX historical packed phase-two pilot covered all five
old stream frames byte-exactly. A real CLI limit-eight pilot avoided decoding
5/8 payloads while checking all hashes. These are bounded pilots, not full release.

The formerly failing QQQ source query recovered at 2 GiB and its exact old failed
receipt was sealed. Later bounded source reviews examined residual clocks in
SCOAW, UKOMW, ASTR, FMIVW and APPHW. Wide penny books and changing eligible-trade
denominators explained tested diagnostics without demonstrating a reconstruction
omission; FormT eligibility follows the shared extended-hours contract. New
residual clocks still require review. No universal outlier-free claim is made.

Runtime evidence is under native `quote-repair-setup` and
`quote-source-first-setup`: `source-first-validation.json`,
`source-first-extended-validation.json`, `source-first-phase2-validation.json`,
`memory-recovery-probe.json`, `memory-incident.json`, optimization profile/parity
artifacts and dated `residual-review-*` / `residual-source-*` records. Some
artifacts predate retirement of the old phase-two stream and remain evidence
only, not approval of the new extractor.

## 8. Training, decoder retention and exact resume

The active model path is [encoder_v1.py](encoder_v1.py) through
[run_v1.py](run_v1.py) / [campaign_v1.py](campaign_v1.py), not the older generic
next-bar model in `model.py`.

The large MAE has encoder width 512, 6 layers, 8 heads, feedforward 2,048;
decoder width 256, 2 layers and 8 heads. A token inside the encoder represents
one feature column across 64 bars. Training hides about 60% of columns, leaving
7 or 8 visible market-column tokens plus summary/time/latest-bar tokens.
Full-input `encode_full` supplies all 18 market columns and returns **one
512-dimensional vector per 64-bar window**, not 512 tokens. Feature attention
is bidirectional inside an entirely historical window; there is no future bar
input. Hidden target columns are never passed into encoder attention.

The decoder receives the single embedding, requested column identities and known
time context; no encoder feature-column skip path bypasses the bottleneck. It
reconstructs scaled continuous `[B,M,64]` columns. This is reconstruction, not
already a phase-two autoregressive future forecaster or guaranteed-valid OHLC
generator. `prepare_batch(mask_rate=0)` / `encode_full` are downstream interfaces.

**Both encoder and decoder are saved** in `model.state_dict()` under the full
`MaskedAutoencoderV1`. Preserve the matching config, normalization and data
contract. Do not load a large checkpoint with default 256-wide config or discard
the decoder when exporting the encoder for phase two.

Epoch, best and resumable-last states include model, AdamW, scheduler, epoch,
global step, phase, history, best loss/epoch, probe evidence, Torch CPU RNG and
all CUDA RNG states. Do not claim the live checkpoint contains additional Python/
NumPy RNG fields merely because transition evaluation restores those RNGs too.
Checkpoint replacement is atomic; existing immutable epochs/parents are retained.

The recorded campaign is large/BF16/SDPA, batch 4,096, 32 loader workers,
prefetch 2, 50 total epochs, full validation each epoch, cosine LR 1e-3 to 5e-5,
1,000 warmup updates, losses every 10 and metrics every 1,000 steps.
W&B identity remains `mehdifaraji/bargpt-v6-latest-bar-overnight/1rpdtas2`,
named `train-01`, even though the current resume output is `train-05`.

The epoch-24 overfit stop was intentional. The user authorized resuming from its
last checkpoint and increasing consecutive-overfit patience from five to
**eight**, preserving history/streaks. No-learning three and nonfinite safeguards
remain. Do not revert to older documented defaults, reset evidence or relax
another intentional guard stop without user instruction. Cached GPU utilization
is not live GPU telemetry when the native probe is unavailable.

Historical original-data evidence explains that policy change: best epoch 17
was `0.1201952651`; epochs 20-24 were all over 2% worse while training loss had
improved over 1%. Epoch-24 last and epoch-17 best were preserved, and `train-05`
resumed `train-04/checkpoint_last.pt`, not random initialization. Later original-
data bests included epoch 26 `0.11527411391500052` and epoch 32
`0.1138581567254802`. These are retained historical observations, not scores on
the repaired cohort. An independent real epoch-25 CPU serialization probe
verified step/scheduler update 1,136,000 and 121 AdamW states; its runtime proof
is compatibility evidence only, not an activated migration.

## 9. Split, calibration and corrected-data handoff

The actual original combined split is `chronological-80-20-v1` with cutoff
**2024-04-16T04:00:00Z**, not phase two's 2026 rule or the old code's default
2025-Q4 cutoff. The sealed original split records:

| Item | Count |
| --- | ---: |
| Train records | 186,054,139 |
| Validation records | 47,494,183 |
| Full contexts purged across boundary | 369,428 |

Split hash: `7cb71f94f0873d1cf6704226ec3652ea611e9d5653b47a191b5931cd12359f04`.
Calibration used a declared bounded training-only sample of 8,017 records.
Its median/scale vectors are recorded in the sealed runtime snapshot. The
derived mmap `loader-index-v1-*` is metadata only; it cannot redefine membership
or bypass payload size/hash/record checks in [data_v1.py](data_v1.py).

Corrected handoff is implemented as preparation APIs, not automatic activation:

1. Wait for **both** v4 members and bounded release/count/schema/coverage plus
   targeted source/content/hash checks. Publish the explicit v4 cohort.
2. [quote_handoff_v1.py](quote_handoff_v1.py) preserves exact ordered original
   membership, cutoff, full-context purge and transitive ancestry. Rebind the
   original six activity median/scale entries; do not refit trained input units.
3. Preserve model/optimizer/scheduler/epoch/step/RNG state in an independent
   prepared checkpoint. Separate old-domain history, best and probe evidence.
   The strict transition field makes old trainers refuse the prepared state.
4. [data_transition_v1.py](data_transition_v1.py) validates actual split,
   calibration and science settings, then runs fixed untrained/trained probes
   and trained full validation before any new update. Restore Torch CPU/CUDA,
   Python and NumPy RNGs; reject partial/nonfinite results or changed trained
   tensor bytes. Save a pending checkpoint before the long baseline and support
   interrupted baseline resume, including baseline-best with no new epochs yet.
5. [checkpoint_transition_v1.py](checkpoint_transition_v1.py) fsyncs/reloads an
   independent CPU checkpoint copy, proves nested retained state/file hashes,
   and rehashes the original parent. Its explicit reporting bridge permits only
   certified dataset/split/calibration path changes while retaining the original
   W&B ID/name/lease root. Ordinary signature/lease checks stay enabled.
6. Drain the old owned controller/child at a complete checkpoint boundary; only
   then pin the audited new source and use the explicit `--resume-training`
   plus `--data-transition-proof` entry. Verify real loader paths, monotonic
   steps, original online identity and new-domain baseline before claiming switch.

Preparation, CPU orchestration tests and W&B bridge files are not proof that the
live trainer switched. Never run a second full validation on its GPU alongside
healthy training. Original and corrected-domain best losses are not interchangeable.

## 10. Roots, ownership, retirement and evidence checklist

| Root | Contract binding |
| --- | --- |
| Original base | `349a37a54442347628b9d59b61d22543e84f686a1fa92a05140f9b75b77b09b6` |
| Original supplement | `ab89fd26b1c6cec7f218868b939e84dc282f0494bdf57a3f9595435452569d14` |
| v4 base | `c202a7870c199e9a8faf919b6dcc1b41f3fa54c7925e4091b5054c007f06bdb7` |
| v4 supplement | `5c94c73da4a1edb04ca9b7b6fcc08e895cc252366811cde24e46a397d1d4476c` |

Native tasks: `BarGPT-Quote-Source-First-v1`, its read-only
`BarGPT-Quote-Source-First-Panel-v1`, and trainer
`BarGPT-V6-Overnight-20261108`. Repair W&B is
`mehdifaraji/bargpt-quote-repair-v1/58dfae4f`.
Use `quote-source-first-setup/health.ps1`, `campaign.json`, `process.json`,
`child.json`, per-root progress/ledgers and timestamped incidents for a bounded
manual check. Verify PID creation/command, not a remembered PID. Old stderr can
contain resolved failures; compare current failure fields and fresh deltas.

Retired `BarGPT-Quote-Repair-v1`, `BarGPT-Quote-Guard-v3` and old quote-v1/v2/v3
partial roots are not current releases. Keep their replacement STOPs, receipts,
caches and incident proof, including the
`REPLACED_BY_SOURCE_FIRST_V1:f2b6f270a` markers. An owned engineering drain can
remove only its own exact marker after verified shutdown; never clear a human
or retired-job STOP. The old phase-two generator is disabled; its dataset
and queued repair were explicitly cancelled. Do not restart it as a handoff step.
See [PHASE2.md](PHASE2.md) for cleanup and replacement details.

Before any activation, reconcile root identities and source revisions, release
states, sample/split/calibration seals, quote invariants, unchanged trade/time
fields, complete checkpoint ancestry and exact online run identity. Preserve
unrelated source changes and immutable evidence. Generic repair/guard examples
in older portions of [QUOTE_REPAIR.md](QUOTE_REPAIR.md) and older patience text
in [OVERNIGHT.md](OVERNIGHT.md) are historical where they conflict with the
explicit current v4/eight-validation contract recorded here.

## V3 price-relative validation diagnostics (2026-11-12)

Keep every existing `validation_full_raw_mae` metric. New price-only means use
`validation_full_price_mape_pct/<frame>/<channel>`. The corresponding median and
P90 keys are `validation_full_price_median_ape_pct` and
`validation_full_price_p90_ape_pct`. Each also has a `_by_price_band` namespace,
with `<band>/<frame>/<channel>`. Only trade/bid/ask OPEN/HIGH/LOW/CLOSE appear;
volume, sizes and counts never appear in these metric namespaces.

The numerator is the absolute error AFTER the v3 inverse converts decoder output
back to sample-basis USD prices. Divide each observation by its own actual USD
price and multiply by100; then average observations, never batch means or a
ratio of aggregate dollar MAE to aggregate price. The original validity mask is
retained. Nonfinite/nonpositive actual prices are excluded and counted. Nonfinite
eligible predictions cause the affected aggregate to be unavailable with explicit
overflow counts. Empty groups are unavailable, not zero. Bands follow the actual
split-adjusted per-observation price: below1,1–5,5–20,20–100,100andabove; lower
bounds inclusive. Stored occurrences are observation-weighted as in raw MAE.

Mean APE uses all eligible values directly in Float64. Median/P90 use nearest-rank
streaming logarithmic histograms; these are approximate order statistics, NOT
exact percentiles. 8192 bins cover1e-9..1e9 percent, zero has an exact separate bin,
and an out-of-range percentile is unavailable. Bin relative width~0.507percent
is logged in `*_price_relative_quantile_bin_relative_width`; midpoint estimates
are within the selected bin. Histograms consume~27MiB per accumulator and merge
counts rather than averaging percentiles. The existing bounded asynchronous CPU
reducer merges price statistics alongside raw MAE after the SAME model forward.
No extra validation forward, training objective or normalization change.

Reporter continues logging through its existing W&B identity/lease and unchanged
optimizer/global-step mapping. New keys are absent before first computation;
no historical zero filling or backfill. The first full new-metric validation is epoch20, after its training completes
and BEFORE its validation. Prior epochs retain their original history.
`run_price_metrics_upgrade_v3.py` waits for the epoch20 complete-training checkpoint in phase=validation,
with epoch19 history and continuing prior learning guard, then uses an exact owned STOP,
checks every nested checkpoint state hash, resumes into an independent output
with the original command arguments, and verifies the initial resumed checkpoint
and original W&B identity. Any human STOP, newer saved state, identity change or
intentional learning-guard stop fails closed. Source synchronization must follow
local tests, commit and push; do not edit the active immutable snapshot.
