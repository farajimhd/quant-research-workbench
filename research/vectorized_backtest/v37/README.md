# V37: single-factor risk ablation of V35

Fresh Aug 4 local experiment: population 1024, seed 2020, 6144 initial
candidates (3072 templates + 3072 calibrated random), 10 generations, batch 1024.
V35 crossover, mutation, speciation, objective, archives, sizing and fees remain.

| Version | Planned stock risk cap | Stop-distance limit |
| --- | --- | --- |
| V34 | $300 | 20% |
| V35 | uncapped | 30% |
| V37 | $300 | 30% |
| V38 | uncapped | 20% |

V37 changes only the dollar risk cap to $300, plus experiment length.
Checks apply to CPU/CUDA execution and candidate eligibility in both phases.
Only the local phase launches for this comparison. No financial result caches or
evolved candidates initialize the new run. Prepared fresh candidates and RNG
must match V35 after version-name normalization.

The hidden deferred launcher audits predecessor seals, exact owners, locks and
successful exit before any Torch/CUDA work. Laptop jobs run serially after V36:
V37 then V38. Short 64-clock batch/parity and GPU boundary checks precede each
run; they are not full-session timing qualifications. Preserve 12 GiB host
reserve plus 8 GiB load admission. The detailed Phase 1 panel includes initial
family tables and compact T/R/M/X labels. Generation-10 comparisons include V34,
V35 and available V36/37/38 results, with execution/crossover differences shown.
This single-session in-sample ablation does not establish generalization.
