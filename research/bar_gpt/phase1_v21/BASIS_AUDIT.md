# Activity optimizer-coordinate audit (2026-11-22)

Hypothesis: a fixed orthonormal temporal basis separates the window-average
activity component from variation, reducing level drift induced by Adam updates
to a change-only objective. This is not a capacity change: the same single
affine 512D decoder has identical parameter count, and no references or extra
embedding fields enter it. Price rows retain their native parameterization.
The alternative remains experimental and is not wired into full training.

Source `a7fcd5c4ff017a1c2b8e954c911838472b7ceb4d`, runtime
`phase1-v21-basis-audit-20261122`, retained parent SHA
`a5279ed6fd2fd6ca44cf5ab14573a4383f9f66a589ed3d8c37c64caad28300d5`.
Two CPU tests verify orthogonality, function/embedding-gradient parity,
parameter count and zero DC gradient for unconstrained differences. Checkpoint
parameter names differ so legacy restores cannot silently misinterpret them.
The immutable workstation snapshot verified 1,526 files before execution.

The audit uses the first 4,096 frozen training windows, both 16-visible and
64-visible embeddings, source-valid adjacent pairs and the existing activity
change MSE/direction BCE coefficients. It takes one fresh AdamW step at 1e-6
with norm clipping at one. This is a component diagnostic, not full-objective
training or an audit of saved optimizer momentum. Checkpoints remain unchanged.
Initial price outputs match exactly; maximum activity-code discrepancy is
3.06e-5. Both variants reduce their component objective after the one step.

Mean absolute window-level drift in log activity:

| Visibility | Constraint | Native head | Basis head |
|---|---|---:|---:|
| 16 | None (diagnostic) | 1.118e-5 | 3.065e-8 |
| 64 | None (diagnostic) | 1.110e-5 | 2.501e-8 |
| 16 | Existing zero floor | 1.283e-5 | 1.921e-5 |
| 64 | Existing zero floor | 1.121e-5 | 1.397e-5 |

Without the floor, the basis reduces drift over 300-fold. With the actual
floor, it worsens drift. There are 6,161 negative activity codes with 16 visible
bars and 1,146 with 64 visible bars, out of 1,572,864 activity outputs per mode.
The constraint breaks the translation invariance of the change objective.
This is demonstrated in this controlled update, not established as the cause
of the full-run regressions.

Decision: reject a basis-only full-training launch. A possible next controlled
ablation is computing the auxiliary activity-change objective before the floor,
while preserving the raw-output floor and the existing unconstrained level
penalty. It needs matched training/validation and worst-group checks; it is not
implemented or qualified here. No prediction clipping, valid-example exclusions,
or deployment semantics were changed by this audit.

Reproduce from the pinned snapshot with `PYTHONDONTWRITEBYTECODE=1`:

```powershell
python -B -m research.bar_gpt.phase1_v21.basis_audit --output D:/TradingML/runtimes/bar_gpt/NEW_IMMUTABLE_AUDIT --source-revision a7fcd5c4ff017a1c2b8e954c911838472b7ceb4d --checkpoint D:/TradingML/runtimes/bar_gpt/phase1-v21-body-20261121/body_direction/checkpoint.pt
```
