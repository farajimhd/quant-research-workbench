# Retained historical symbol interval repair

[Controller](13-campaign-controller.md) · [Identity prerequisites](13v-identity-prerequisites.md)

## Launcher path

Source preparation and fresh launch accept `--repair-retained-symbols` with
one or more `--retained-figi` values. Requests must contain 1–1,000 unique uppercase
stable FIGIs. Frozen execution rejects this repair selection. The repair runs
under source ownership and the Gateway dictionary claim before SEC/event
preparation and graph freezing.

The path reads retained Gateway inventory, event payloads and coverage directly
from ClickHouse. It makes no provider request and opens no SIP flatfile. It uses
the existing Gateway interval builder, including curated timeline corrections
whose provider signatures independently match the retained payloads.

## Required evidence

Ordinary mapped intervals require active, unambiguously mapped identities with
completed nonempty retained history. The pending-origin path below additionally
supports independently verified successful empty history. Event identity,
payload hashes, response hashes, normalized
dates/tickers, row counts and date census must match the source coverage.
The successful history snapshot must cover the full requested end horizon and
must not predate a newer inventory update.
The timeline must establish a symbol origin no later than requested context;
history starting later does not authorize guessing an earlier ticker. Every
nonempty historical alias requires an unambiguous canonical binding. The repair
never silently skips an alias.

Inactive lifecycle, origins without verified points, competing mappings, missing history
and venue/issuer transitions remain separate dependencies. Rebuilding symbol
intervals does not by itself establish historical listing eligibility or broker
execution availability. Current conid anchoring and universe admission still run
their existing independent checks.

### Verified dated origins

The launcher can now reuse its explicitly selected `--identity-point` observations
as origin inputs for the same retained FIGIs. It loads the points in one bounded
query and reconstructs/verifies their immutable hashes. Only an unambiguous point
at or before context is selected; no point is projected back to its reported IPO
date. The observation must match stable FIGI, share class, CIK, venue, currency,
stock type and canonical listing binding. Conflicting or inactive evidence blocks.

Covered-empty history additionally requires the original response fingerprint to
match a successful `OK` empty response. `NOT_FOUND` does not prove absence of
ticker changes. Retained nonempty timelines must agree with the point.

Synthesized origin intervals persist as `origin_verified_scope_pending` rather
than ordinary mapped eligibility. Universe admission rejects them with
`dated_origin_requires_listing_scope`. This closes the symbol-origin evidence
path but does not certify uninterrupted issuer/listing/venue eligibility.
The origin input fingerprint is reloaded before and after publication, and a
complete resume inserts nothing.

## Publication and restart

Existing intervals must match the complete desired semantic projection. Conflicts
or extra intervals require explicit reconciliation; this utility does not delete,
tombstone or overwrite them. It inserts only missing interval identities in
bounded batches. Input fingerprints are rechecked before/after publication and
persisted intervals must read back exactly. A complete resume inserts nothing.
Inventory and event/interval metadata have row/byte admission and server result
bounds, and table policy/actual parts must remain on the required SSD.

## Validation

Focused tests confirm normal Gateway kernel parity, no skipped aliases, altered
evidence rejection, missing-origin/inactive blocking, explicit launcher selection
and no-insert resume. An isolated real ClickHouse fixture rebuilt two intervals
and then reused them with zero inserts. Canonical binding inputs were controlled
in that fixture; it does not certify production identity reconstruction.
No production repair, provider acquisition or full campaign was run.

## Historical membership cutoff and current data blockers

The research universe now binds the qualified history cutoff into its source fingerprint
and requires ticker-history coverage through the requested New York date boundary.
Completed status alone cannot qualify later dates. Missing/invalid clocks reject;
stale responses reject. Closed symbol/listing intervals require only their own
earlier end boundary, preserving usable historical prefixes and DST semantics.
The raw response timestamp is checked, but its fingerprint contribution is capped
at the required boundary. Later successful refreshes therefore do not invalidate
unchanged historical membership solely because their operational clock advanced.

The October 14 read-only audit for January 1–October 2, 2026 found:

| Condition | Rejected intervals |
| --- | ---: |
| Missing dated symbol mapping | 2,509 |
| History response predates the required cutoff | 4,547 |
| Missing listing start | 57 |
| Identity mismatch | 7 |
| Open mapping issue | 6 |
| Conflicting/invalid conid anchor | 19 |

Only 75 closed intervals passed this audit; they do not establish full-period
universe coverage. Within the missing-symbol group, 2,351 have no coverage record
bound to their listing, 34 need covered-empty origin evidence, 54 need inactive
lifecycle proof and 70 have source-mapping conflicts. These are repair lanes,
not proof that all raw history is absent or that a repair can safely proceed.

Retained daily universe observations span June 9–October 5 across 65 dates. No
historical listing-observation bank or additional overview/detail tables were
found in ClickHouse. The user confirmed no known additional retained archive.
This evidence cannot certify the January start or fill missing lifecycle/origin
dates. Completing historical identity still requires missing dated reference
evidence and conflict resolution. Provider access remains disabled; no acquisition
or production identity writes were attempted during this audit.
