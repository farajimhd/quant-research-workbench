# Preparing source context before graph freezing

[Controller](13-campaign-controller.md) · [Context requirements](09a-context.md)

## Static execution dependencies

Fresh launch now derives the initial source preparation floor from the selected
rolling activity windows, prior-session RVOL, short-pressure context and an
explicit V7/technical rebuild origin. The supplied context start may request
additional earlier data; it cannot truncate a definition-derived requirement.
The same derived floor is passed to historical symbol repair and fresh single
or collection preparation.

This does not create a seed or infer an IPO/reset date. Missing compatible state
still requires qualified evidence or an explicit rebuild origin. Rolling-parent
limits and the canonical-history floor still block invalid requests.

## Discovery-driven dependencies

Float disclosures can require measurement-date prices earlier than the initial
market context. Initial preparation discovers those exact dates, then asks the
fresh-launch source-preparation callback to prepare their required source union
before it takes the expanded canonical/reporting inventory and freezes its epoch.

The callback acquires a new source claim while retaining the dataset claim,
propagates existing acquisition/migration opt-ins and SSD staging parameters,
drains work, closes source ownership and returns to independent inventory
verification. Single launches and collection members use this same callback.
Frozen run/resume cannot acquire newly discovered inputs.

## Sparse source authority

Explicit source-session selection now pushes the dates into both source statistics
and import-manifest SQL. It rejects unrequested readback rather than hashing or
repairing every intervening date. Daily source hashes retain their original
definition; sparse aggregate hashes now describe the selected date set.
Previously frozen plans that captured broader aggregate hashes require explicit
requalification if their verification differs; no legacy-hash fallback is added.

## Validation and limits

Tests cover longer-window and rebuild-origin floor expansion, sparse query scope,
invalid-date rejection and late price-date preparation before freezing. Existing
launch and collection preparation regression checks pass. These changes do not
resolve missing original reference evidence, qualify legacy states or demonstrate
production throughput. No provider acquisition or historical campaign ran.
