# Independent market publication

[Overview](../certified_research_dataset_design.md)

## Approved policy

Market products need not wait for unrelated missing SEC, float or short-data
publication evidence. A market-only seal covers only its declared products;
it never certifies a complete combined research dataset.

Current reviewed conids may anchor retrospective research identity. This is an
explicit assumption, not proof of historical broker permission. Known IPO,
delisting, rename and contrary security mappings remain constraints. Unresolved
conids and conflicting/ambiguous identities are quarantined with counts.
The policy does not update live Reference Gateway listings.

Float, fundamentals, short volume and short interest remain unknown unless
both the value and historical public availability qualify through their
existing reference producers. Never substitute zero, current values, future
filings or a reporting period's end for an availability timestamp.

## Implemented first publication scope

`--product-scope market_bars` prepares bars, broker liquidity and rolling
activity using the existing shared market aggregate and normalized ClickHouse
tables. It removes SEC/float/short discovery and seed preparation from this
scope. Missing references are not fabricated to make a full graph pass.

`--identity-policy current_conid_retrospective_v1` selects the approved identity
assumption and requires `--conid-review-hash`. The assumption, underlying
review/current metadata, boundaries and quarantine census are hash-bound.
Membership provenance explicitly records the assumption. The default remains
the earlier strict dated-identity policy, preserving existing frozen campaigns.

The earliest retained observation is not an identity birth date. The assumption
extends it into earlier unknown history; a known IPO still clips the interval.
Known rename boundaries remain separate rather than projecting today's ticker
over a contrary historical ticker. Source spelling is resolved from the compact
canonical ticker/day index, without rescanning events or fetching REST trades.

```text
certified canonical events + repaired flags + reviewed identity
    → one market aggregation
        → bars + broker liquidity
        → rolling activity

qualified dated reference sources → reference products (separate scope)
compatible state + corporate history + bars → indicators / V7
```

Continuous indicators and causal PM/REGULAR/AH V7 checkpoints still require
their legitimate state/adjustment inputs. The first `market_bars` scope defers
them explicitly; it does not initialize an empty state or claim those products
were delivered. Connecting their later execution to already published bars
remains work; avoid regenerating bars as a workaround.

## Unknown representation

`arte.research_reference_availability_v1` is a compact auxiliary MergeTree on
`live_market_ssd`, with lossless ZSTD/T64 codecs. Its keys are dataset, security,
family and inclusive date range. `status=0` means unknown; the reason and frozen
evidence hash are retained. It contains **no numeric feature values**.

The market-only scope writes one range row per security for each of
`fundamentals`, `float`, `short_volume`, `short_interest`. This is conservative:
it does not promote existing unqualified rows. Later qualified reference
products retain their separate immutable dataset/receipt boundaries.

Downstream consumers must bind the dataset and requested product scope, then
require qualified reference receipts for numerical features. Availability rows
never authorize a numeric lookup or a zero fill. A market-only campaign's
completion does not satisfy a request for a complete combined feature set.

## Launch and resource behavior

Use the existing `scripts/prepare_certified_research.py launch` or
`launch-collection`, with the two scope/policy parameters above, a new dataset
UUID, explicit context/end dates, explicit secrets/runtime paths and a qualified
capacity profile. `--fundamental-start-date` is not required for `market_bars`.
The profile must match the additive installation and include the bounded
`reference_availability` write product; rates are not invented or silently
borrowed from other products.

With no acquisition/repair flags, the independent launch checks only its
selected canonical/reporting source range and does not acquire the global SIP
writer lease or invoke SEC acquisition. Thus a completed source month can be
consumed while later source months are being migrated. Selected dates still
must have completed flag repair; active mutations of their authority block
publication through existing source guards.

Explicit future downloader/import opt-in remains on the same launcher.
The active long-running source worker is not restarted merely to deploy this
derived-product separation. No training, optimizer execution or exports are
part of this scope. Historical feature values are prepared only in ClickHouse.
