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

Only active, unambiguously mapped identities with completed nonempty retained
history are accepted. Event identity, payload hashes, response hashes, normalized
dates/tickers, row counts and date census must match the source coverage.
The successful history snapshot must cover the full requested end horizon and
must not predate a newer inventory update.
The timeline must establish a symbol origin no later than requested context;
history starting later does not authorize guessing an earlier ticker. Every
nonempty historical alias requires an unambiguous canonical binding. The repair
never silently skips an alias.

Inactive lifecycle, covered-empty origins, competing mappings, missing history
and venue/issuer transitions remain separate dependencies. Rebuilding symbol
intervals does not by itself establish historical listing eligibility or broker
execution availability. Current conid anchoring and universe admission still run
their existing independent checks.

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
