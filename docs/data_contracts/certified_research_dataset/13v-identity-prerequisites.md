# Dated identity prerequisites

[Controller](13-campaign-controller.md) · [Completion audit](13i-completion-audit.md)

## Execution order

Fresh `launch` and `launch-collection` install/verify the compact schema, prepare
explicit dated identity points, qualify SEC lineage, prepare canonical source
and reporting repair, then assemble the derived graph. No graph is frozen after
an identity failure. `prepare-sources` also supports this prerequisite; its
identity table must already exist through `install-schema`.

## Launcher options

| Option | Contract |
|---|---|
| `--identity-point DATE,TICKER,COMPOSITE_FIGI[,CIK]` | Repeat for 1–1000 unique ticker/date requests; uppercase symbol and 12-character FIGI; date from canonical-history origin through universe anchor |
| `--acquire-identity-missing` | Explicit reference-overview acquisition after resubscription; requires selected points; default off |
| `--workers` | The existing bounded executor pool; no nested provider pool |
| `--acquire-missing` | Separate canonical flatfile acquisition permission; does not enable reference acquisition |

Frozen `run`, `resume` and collection resume/verification reject these options.
Normal reuse does not construct the provider or probe credentials. Missing
observations without reference opt-in fail before market preparation. Invalid
or duplicate requests fail before ClickHouse client construction.

## Persistence and authority

Each point uses the existing `research_listing_observation_v1` normalized,
losslessly compressed SSD contract. Stable identity is reconciled against the
requested FIGI and optional CIK. Publication uses dictionary ownership; every
new point must pass independent persisted readback. Repeating the request
verifies/reuses the immutable point without an insert. Conflicting observations
require explicit resolution rather than overwrite.

A point proves identity/lifecycle observations for its date. It does **not**
create a continuous eligibility interval, historical broker availability, or
fundamental availability evidence. Automatic missing-point discovery and
certified interval reconstruction remain required. No SIP REST recovery is
introduced, and no provider acquisition was run during validation.
