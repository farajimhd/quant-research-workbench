# V35: Aug 4 risk-limit comparison against V34

V35 duplicates V34's calibrated random/template initialization, recurrent
networks, objective arithmetic, reproduction, speciation, sizing, fees and
resource bounds. This laptop-only experiment uses Aug 4, population 1024,
seed 2020, 6144 initial candidates and 50 local generations. No validation or
global phase is launched by this experiment's controller.

Requested changes:
- Remove the $300 per-stock planned-risk ceiling from entry/addition checks
  and candidate eligibility. Preserve planned-risk calculation and display.
- Increase the stop-distance limit to 30% in CPU and optimized CUDA execution,
  configuration validation and eligibility. Terminal validity remains required.
- Use compact ancestry percentages while retaining mutation/crossover counters:
  `G001-T27.5-R72.5-M009-X024/47858`. T/R are ancestry-path percentages, not
  unique founders or inherited-neuron percentages. Exact counts stay in saved
  pedigree metadata. M/X retain their previous primary-lineage semantics.

`Execution.maximum_position_risk=0` explicitly denotes a disabled dollar-risk
ceiling; V35 rejects configurations attempting to restore a finite ceiling.
`risk_protection=True` continues to enforce valid stops, the 30% distance
limit, and risk reporting. It does not reinstate the removed dollar ceiling.
The 1000-dollar entry and 200-dollar addition sizing caps are unchanged.

Both phases use eligibility: Phase 1 checks its local session; Phase 2 checks
all training sessions. The current experiment runs Phase 1 only. Objective
concentration/drawdown penalties and unrelated genome bounds are unchanged.

Press `h` in the detailed Phase 1 observer for initial/current family tables.
All 6144 initial candidates are replayed under the changed execution rules;
no V34 financial results, fitness or evolved candidates initialize V35.
Source-normalized initial candidate payloads and RNG are checked against V34.

Use short boundary tests and the finite 64-clock mixed-candidate GPU diagnostic
before launch. This diagnostic is not a full-session timing qualification.
CPU/CUDA boundary tests cover accepting planned risk above 300 dollars,
accepting a 30% stop and rejecting a stop above 30%. Preserve the original
12 GiB laptop host reserve plus 8 GiB admission and GPU headroom checks.

After completion, the launcher compares generation 1 and 50 against preserved
V34 results. Execution limits intentionally differ; all other compared
contracts must match. This is an in-sample risk-policy experiment, not a
claim about generalization or live profitability.
