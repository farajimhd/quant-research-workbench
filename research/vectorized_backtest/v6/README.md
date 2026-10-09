# V6 causal compact-market optimization (implementation in progress)

V6 is an isolated source copy of V5 plus the new training materializer. The
inherited search/replay modules have not yet been adapted or qualified for V6.
Do not launch optimization from them. V5 immutable deployments are untouched.

First runnable producer:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.vectorized_backtest.v6.torch_backtest.materialize --sessions D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v4/campaign_inputs/20261006-source-aligned/sessions.json --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v6/materialized/volume30-top10 --workers 2
```

The producer reads only the 30 training sessions and their previous regular
session context. An explicit certified context manifest/ledger is required when
the prior date is outside the main source build. Price eligibility is previous
regular-session final traded canonical bar close, adjusted for opening-known
splits, inclusive $0.80--$50. It precedes all current-session volume reads.
Missing context fails the session closed rather than substituting a close.

Rolling share volume includes the current completed second and the preceding
29 elapsed seconds. Zero-volume/unavailable listings do not fill empty slots.
Stable listing identity breaks ties. Bounded session workers query certified
ARTE bars, validate source counts/keys/content and storage placement, and write
top-N blocks, rank arrays and sparse backing rows beneath the runtime root.
Completed output resumes only with identical arguments/source/output hashes.
Validation is sealed; no validation market data is opened.

These first blocks are deliberately marked `ready_for_replay=false`: feature
history, execution quotes and held/pending-order binding remain required.
V5 must not stop merely because these preliminary blocks are complete.

Remaining implementation: compact GPU replay with identity-preserving holding
slots and pending orders; six lifecycle programs including adds/reductions;
semantic sampling and conditional/frozen genes; full-training generation
evaluation with bounded session concurrency; lower-tail-profit objective;
50-row ranking pages and session/complete-position diagnostics; equivalence,
causality and financial qualification; measured population/generation report.
Full optimization requires the user's final parameter decision.
