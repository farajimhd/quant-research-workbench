# Frozen window representation diagnostics (2026-11-22)

These are diagnostic readouts, not replacement decoders or forecasting tests.
No repaired full-training candidate is qualified by these results.

`window_probe.py` fits training-only linear ridge probes on frozen 512D window
embeddings. It uses 114,599 training and 11,146 development windows from the
existing sealed split and supplement. Each channel task uses complete
source-valid windows and reports inclusion counts. Probe/native comparisons use
identical valid examples. A single fit spans all timeframes; per-frame results
do not use separately fitted readouts. Labels measure price/activity levels,
candle changes and adjacent activity changes. Controls include untrained
encoders, raw summaries and training constants. There are no future labels.

Paired run: `phase1-v21-window-probes-paired-20261122`, source
`fbfbe2c330bfb2a823db7260c5e2518fb032178d`, W&B `o0zme4nz`.
Predeclared ridge sensitivity: `phase1-v21-window-probes-ridge0001-20261122`,
source `880e2ea3f53f0bcc0d00e0f0c512776e839ab93d`, W&B `w4dxqjja`.
Both are in `mehdifaraji/bargpt-phase01-v21-full20`; remote readback confirms
finished runs. Runtime `result.json` records checkpoint/data hashes and counts;
campaign `window-probe-comparison.json` preserves group comparisons.

With ridge 0.01, the stopped full checkpoint's daily-volume raw MAE improves
from 1,785,009 under its original decoder to 1,383,319 under the probe, but remains
worse than the retained parent's original decoder (729,291). Its 1s close MAE
changes from $0.3225 to $0.3047. The parent's corresponding probe is $0.3119.
These matched probes do not indicate broad representation collapse. They do
not establish that all relevant information survives: daily bid-count probe
MAE is 56,211 for the stopped model versus 48,687 for the parent.

Reducing ridge to 0.0001 raises stopped-model 1s volume-change moving-direction
accuracy from its native 77.48% to 87.67%, and daily bid-count change accuracy
from 55.58% to 63.34%. However, daily-volume raw MAE rises to 3,925,500 and 1s
close MAE to $0.3910. The parent shows the same tradeoff: volume-change accuracy
79.25% to 89.04%, but daily-volume MAE 729,291 to 5,607,993.

Activity-change information is therefore recoverable better than the existing
readout extracts, while weaker probe regularization seriously harms absolute
scale. This supports investigating readout conditioning jointly with scale
retention; it does not support replacing the decoder with these probes or
claiming that LR alone explains the training regressions. Regularization was
not selected using development scores. All checkpoints and STOP markers remain
preserved. No training process was left running by these diagnostics.
