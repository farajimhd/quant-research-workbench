# Scoped SEC acquisition through the unified launcher

[Controller](13-campaign-controller.md) · [Completion audit](13i-completion-audit.md)

## Execution dependency

Fresh `launch` and `launch-collection` prepare dated identity points, qualify
existing SEC acquisition lineage, and check required SEC coverage before event
preparation and graph freezing. Missing SEC coverage blocks unless
`--acquire-sec-missing` is selected. `prepare-sources` includes this SEC dependency
when `--fundamental-start-date` is supplied; without it, the existing market-only
preparation remains available.

The opt-in invokes the existing complete 21-stage `sec_historical_gap_fill.py`
chain. It does not select finalize-only, partial stages, source limits or
continue-on-error. SEC acquisition permission is independent of Massive
flatfile/reference permissions. A successful child exit is followed by independent
stage-lineage qualification and full required-family coverage readback.

## Parameters and bounds

| Launcher option | Meaning |
|---|---|
| `--fundamental-start-date` | Explicit earlier SEC source/context bound; no implicit restart at 2019 |
| `--acquire-sec-missing` | Acquire missing SEC prerequisites; default off |
| `--sec-runtime-root-ch` | Verified CH mount alias for the runtime's `sec` subdirectory; required for catch-up |

The SEC command derives its inclusive required UTC horizon from the existing
reference coverage contract, then passes the corresponding exclusive end to the
SEC script. SEC workers are bounded by eight, selected workers and the number of
query-memory reservations fitting the global budget. The scoped SEC script
requires query threads in 1–8 and query memory in 64 MiB–32 GiB.

All generated stage outputs and prepared parts stay below the selected operational
runtime root. The unified handoff explicitly places SEC context products in
`q_live`; standalone legacy routing remains configurable. Known child thread,
memory, worker and SSD-policy options are propagated. Shared HTTP clients inherit
the scoped query defaults, preserving readonly and tighter existing defaults.
These defaults are not server-enforced hard limits: explicit SQL settings can
override HTTP defaults. Representative query/client-memory and transient-disk
qualification remains required before production efficiency is claimed.

## File and storage preflight

Before any acquisition, scoped execution checks the existing canonical, mirror,
manifest and context table policies/actual parts. It then checks the CH file
authority and reads an exact nonce file from the host runtime through the supplied
alias. The probe is deleted afterward. An inaccessible or mismatched alias fails
before downloads, source writes or campaign manifests.

**Current host dependency:** live CH reports `user_files_path=/mnt/d/market-data/`.
The default `/mnt/d/TradingML/runtimes/...` mapping is outside that authority and
was rejected in a live read-only check without creating a runtime directory.
A verified mount alias inside the allowed directory is needed. No server
configuration, filesystem mount or server restart was performed.

## Validation and remaining work

The actual parent dry-run generated all 21 stages without launching children.
Tests check supported child options, resource validation, exact runtime probing,
coverage reuse, acquisition opt-ins and post-child qualification. A real output
failure stopped/drained the owned child. Live HTTP settings confirmed one thread,
256 MiB query memory and readonly=1.

No provider acquisition or full campaign ran. Missing original fundamental/short
vintages, historical eligibility reconstruction, legacy V7 qualification and
representative capacity evidence remain separate dependencies.
