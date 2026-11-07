# V6 liquid supplement, queued after the base

The original 1 TB campaign keeps its 75/25 liquid/less-liquid policy. A separate
500 GB target component uses the same `bargpt-ordinal-balanced-64-v6-no-halts`
schema, canonical bar rules and 64-bar sample format. It publishes only originally
liquid end-years. It has its own immutable contract and receipts within the same
`bargpt-2019-2025-phase1-v6` corpus family. Exact target bytes are caps: unique
source availability and metadata reserve determine final payload size.

`liquid_universe_v1.py` pins familiar names, including AAPL, NVDA, SPY and QQQ,
and adds the top 512 symbols ranked by historical events per source day. The
sealed supplement plan lists included and unavailable requested names. This is
a historical activity universe, not a claim of historical Nasdaq-100 membership.
Ticker-years retain the base's original activity classifications.

`supplement_v1.py` keeps orchestration separate from the shared canonical SQL:

1. Extract hourly/daily spans first, with all unique sliding origins and the
   existing quote carry, valid-trade and backward split-adjustment semantics.
2. Allocate remaining space equally across 100ms, 1s, 10s and 1m, then equally
   across eligible ticker-years. Chunks are disjoint across year boundaries.
3. Sample at most 20% of fine origins. Anti-join every candidate against the
   completed base using ticker, timeframe and first/last real bar clocks. Parent
   payload hashes are checked before comparison. No duplicate parent window can
   be republished as additional training data.
4. Visit every initial ticker-year allowance before redistributing unused
   capacity. Scarce hourly/daily inventory is never padded or duplicated.

The implementation reuses shared ClickHouse aggregation and packing, uses
64 query workers and 16 bounded I/O workers, and writes <=32 MiB NPY shards.
Each source cursor advances only after its corresponding receipts are durable.
Quotas, byte reservations, phase allocation and checkpoints support restart.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u research/bar_gpt/v5/run_liquid_supplement.py --source-revision <committed-revision> --prepare-only
python -B -u research/bar_gpt/v5/run_liquid_supplement.py --source-revision <same-revision> --panel
```

The second command waits for the base's successful `result.json` and released
controller lock. It does not start after STOPPED or FAILED. During extraction
it holds the base lock so the parent anti-join inventory remains immutable.
The scheduled workstation wrapper retains stdout, stderr, process identity and
exit status. A STOP file in the supplement root drains it independently.

The sibling `phase1-v6-cohort/cohort.json` references both components with
relative paths. No running base contract is modified. The app discovers available
members and labels samples by component. The trainer resolves both components
from the existing base dataset argument, requires successful component completion,
then creates one chronological 80/20 split and purges full contexts across the
shared cutoff. Old base-only split/calibration/checkpoint contracts cannot be
reused for the enlarged cohort. Model size, masking, BF16 and optimizer settings
remain unchanged.
