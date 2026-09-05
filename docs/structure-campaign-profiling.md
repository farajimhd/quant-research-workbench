# Structural checkpoint campaign profiling

Run `scripts/profile_structure_checkpoint_campaign.ps1` on DESKTOP-SAAI85T with
the ml4t environment active. Pass `-Binary` pointing to the new profiled
`structure_checkpoint_campaign_v18.exe`; put `structure_checkpoint_cost_probe.exe`
beside it. A deployed source mirror without Git metadata also requires
`-SourceCommit` with the full committed source revision. The Python launcher
provides advanced bounds through `--help`.

The launcher validates dependencies, binary protocol, runtime location, source
identity, sample availability, and ClickHouse connectivity before requesting a
graceful stop of the current campaign. It waits up to 30 minutes for matching
supervisor and native worker processes, including orphan workers. A stop timeout
leaves the stop request active and exits without killing workers or starting the
next run. The existing campaign's completed checkpoints remain usable.

After shutdown, it reads the latest certified JUNS and SUGP checkpoint available
in the current set into the workstation runtime directory. The offline probe
measures decode, hashing, serialization, cloning, seeding, and extraction. It
requires the certification hash, serialization roundtrip hash, and seeded engine
hash to agree. A missing ticker is reported as unavailable; failed parity stops
the workflow. Captures remain on the workstation.

It then creates a uniquely named `qmd_structure_profile_*` ClickHouse database.
Only profile checkpoints and their registry use that database. Historical events
continue to come from the canonical imported SIP authority. Native campaign
storage validation requires `live_market_ssd`. No production database is purged,
and profile sets cannot become production checkpoint authority accidentally.

The default comparison runs 16, 32, 64, then 96 process workers against one frozen
96-ticker sample for August 21, 2026. Candidates come from the existing tradable
universe, with average historical event counts chosen to keep the experiment
bounded. Exact planning must remain within 20 million events per run. If it
exceeds that cap, the launcher stops with the frozen plan location; it does not
silently enlarge or change the workload. Every run uses the same plan and fresh
isolated checkpoint set. Each has a 30-minute execution deadline, followed by a
graceful shutdown window if interrupted. No subsequent comparison starts after
a failure, deadline, incomplete profile, or checkpoint hash mismatch.

`D:\TradingML\runtimes\structure-validation\campaign-profile-<id>\report.json`
records source and executable identity, sample date, isolated database, mature
checkpoint costs, wall time, completed events, checkpoint hashes, retry counts,
host CPU and memory samples, and accumulated worker phase times. Worker phase
times overlap across concurrent workers and must not be interpreted as wall
time. Persistence details separate encoding/validation from send/retry time.
Worker logs retain transport error cause chains and per-day profile records.

These cold, one-day replay comparisons are not a full-history ETA. Fixed run
order can also introduce cache warming effects. Use the mature checkpoint costs
and repeat promising worker counts before choosing production concurrency.
Profiling adds one record per completed day and per persistence request; it does
not change the structural algorithm, admission scores, checkpoint payload, or
certification rules. It does not yet claim to fix the suspected idle-connection
transport behavior.

The full production campaign remains stopped after profiling. Review the report
before creating a successor/resume run with the chosen worker count. No automatic
full campaign restart or checkpoint deletion is performed.
