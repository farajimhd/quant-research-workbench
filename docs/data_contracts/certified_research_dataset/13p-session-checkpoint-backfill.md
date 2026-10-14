# Causal session-checkpoint backfill

[Launcher](13j-launcher-execution.md) · [Checkpoint contract](05-v7-checkpoints.md)

## Launch path

Use `scripts/prepare_certified_research.py backfill-v7` with distinct
`--source-dataset-id` and new `--dataset-id`, plus a qualified
`--capacity-profile`. Optional `--backfill-start-date` / `--backfill-end-date`
narrow the source plan's target period. Source resources, rules, calendar and
concrete market recipe remain frozen. `resume --dataset-id ...` uses the same
stored backfill graph; an existing target must use resume instead of replanning.

The source must be a compatible unified plan with independently certified
ClickHouse bars. Current `bars_v1` rows or retrospective `structural_levels_v7`
without the required source/recipe/receipt proof do not qualify. Acquisition,
retained-file recovery and REST calls are forbidden in this command. Run their
ingestion prerequisites separately before freezing a backfill.

## Execution dependencies

1. Verify the original canonical/reporting day bindings and calculation recipe.
2. Recreate the frozen dated membership and import qualified corporate vintages
   into the target dataset, retaining original values and public-time evidence.
3. For each listing, qualify the preceding causal AH state. If available, replace
   its earlier calculation chain with one `v7_seed` root. Otherwise replay the
   complete declared ancestry; no arbitrary warmup or legacy-seed promotion.
4. `certified_bars` roots verify existing source receipts and actual rows. Bars
   stay in their original dataset; neither events nor bars are copied/exported.
5. `v7_backfill` runs the same causal kernel: prior AH → PM → regular → AH.
   Listing chains are chronological; independent listings use the common bounded
   worker/memory pool. The source's AH checkpoints remain untouched.
6. Verify all three target payloads against their certified input prefixes before
   publishing the campaign seal. Resume revalidates dependencies before reuse.

## Persisted forms and retention

No new table or codec is introduced. Typed locators use the existing compressed
`research_campaign_unit_v1` arguments (maximum 8 KiB), argument hashes and parent
keys. A bar locator freezes the complete source receipt, version and assembly
algorithm hash. Target checkpoints use `research_v7_checkpoint_v1` and the
existing lossless full-state codec on `live_market_ssd`.

Foreign-bar V7 state context additionally records `bars_dataset_id` as a UUID
string. Receipt lineage binds it; restore and seed discovery independently
requalify the referenced bars. Source bars and their receipts must therefore be
retained while a backfill references them. Deletion/mutation fails verification.
This target contains structural checkpoints and their prerequisites; the common
market/reference dataset remains the source dataset.

Capacity admission charges checkpoint writes to the qualified `v7` profile,
plus campaign metadata, membership and reference imports. It charges no new bar
or liquidity storage. Missing/ambiguous source authorities, incompatible recipes,
changed fingerprints or unqualified legacy state block rather than fall back.

## Bounded validation

A real ClickHouse fixture generates two days, backfills only day two from the
qualified day-one AH, compares all three target state hashes with uninterrupted
generation, and discovers the result as a compatible future seed. It verifies
zero target bar/liquidity rows, no canonical-event scan, and unchanged seal with
zero inserts on resume. Synthetic source admission and membership are confined
to the isolated test database. This proves the runnable calculation path, not
production coverage or full-universe throughput.
