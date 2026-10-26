# V53 action-level crossover experiment

Authorized scope: August 13, 2026 only, laptop GPU, no validation sessions.
Default budget is 10 generations, population 1,024 after evaluating all 6,144
fresh founders (3,072 templates and 3,072 calibrated random programs), seed 2021.
Current fitness is net profit - gross losing-position dollars - 2 * equity
drawdown, including open positions. Bid/ask fills, fees and risk guards are unchanged.
The original net-only pilot is preserved under its immutable v1 deployment.
This single-session experiment cannot establish out-of-session generalization.

## Reproduction

Rank eligible active parents, including negative penalized fitness. Keep the
10% elite / 20% fresh / 70%
intended offspring allocation. Ordinary typed-mask crossover is unchanged.
One quarter of intended crossover slots compose exactly one rotating action,
retaining the base parent's other actions and financial policy. Composition
modes are OR 40%, AND 20%, XOR 20%, XNOR 20%. Mask crossover retains its existing
uniform/union/intersection/XOR modes. XNOR is not a bitwise float operation.

Mate selection checks the exact child allocation before graph construction:
base strategy nodes + donor nodes for selected actions + six bridge nodes per
action must fit the unchanged 512-node strategy limit. An unavailable compatible
mate or invalid/duplicate child produces a unique mutation of an eligible ranked
parent. If no eligible active parent exists, explicitly counted calibrated
fresh exploration fills missing slots. Founder priors/calibration also apply to
baseline fresh immigrants. No self-mating or ancestry relaxation is introduced.

XNOR fires on equal known parental decisions. Both parents' market validity
must be known; missing data does not establish agreement. Both-no agreement uses
the larger parental size request, never their sum, and XNOR retains the greater
parental minimum age. All original order, quote and financial protections apply.

Accepted crossover children receive no immediate mutation before selection.
Fresh candidates retain the existing 52.5% bounded mutation schedule. Failed
mating uses one parent-mutation route without a second mutation pass.

## Paired mutation diagnostic

At first evaluation, up to 32 pure crossover children per generation are chosen
by deterministic hash order. Mutated counterparts replay the same session with
independent deterministic RNG, separate receipts/cache and no selection effect.
Failures are counted with reasons. Main cohort identity and cache ownership are
preserved. These paired measurements are training diagnostics, not validation.

Each generation seals evaluated-pedigree.json, births.json, offspring-audit.json,
mutation-probe.json, financial rankings, receipts and frozen successor/RNG.
Restart verifies their hashes. The checkpoint resumes only sealed generations.

## Run

From an immutable committed deployment with deployment.json:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.vectorized_backtest.v53.full_launch --source-job <sealed-laptop-v49-job> --output <runtime> --code <deployment> --generations 10 --comparison-source <completed-net-only-v53-runtime>
python -B -m research.vectorized_backtest.v53.pilot --job <runtime>/pilot-job.json
```

Use the pinned scheduled_launch entry for the real experiment so app closure
does not own the optimizer. Write STOP beneath the run directory for graceful
interruption. The all-session/workstation launch is explicitly disabled.

The v2 job binds the changed cost and comparison founder/RNG identities. Founders
are freshly regenerated and replayed, then checked against the original cohort.
No old financial receipts, fitness, offspring, or checkpoints seed the new search.
This comparison changes both the risk objective and negative-score parent admission;
it does not isolate the coefficient effect by itself.
