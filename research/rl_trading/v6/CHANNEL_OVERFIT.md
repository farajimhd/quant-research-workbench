# Own-channel overfit comparison

`run_channel_underfit` keeps the authenticated 128 balanced targets across six
TRAIN sessions and the full market input population. It starts fresh weights
using the previously successful width-512 TCN, hierarchical current action,
five future action heads, allocation and conditional quality supervision.
No development or sealed targets are loaded.

The existing 147 bank fields remain unchanged. The ordered adapter adds 17
changes, 17 availability masks and a boundary flag. Prices, VWAP, EMA and ATR
use `log(x[t]/x[t-1])`; activity uses `log1p(x[t])-log1p(x[t-1])` to handle zero.
Signed MACD uses `(x[t]-x[t-1])/(abs(x[t])+abs(x[t-1])+epsilon)` and RSI uses a
difference. Relative stored indicators are reconstructed onto their physical
scale before comparison. Every comparison uses that channel's own predecessor.
Missing predecessors, invalid source fields and session boundaries are masked.
Elapsed gaps and original absolute context remain inputs. V7 slots retain
their original representation: stable level IDs are absent, so own-level
changes cannot safely be computed from slot indices.

Extra-channel normalization is fitted only on unique strictly prior TRAIN
history rows. The base normalizer is frozen. Source, cache, selection, split
receipts and both normalizers are hash-bound in each new run manifest.

Launch from the laptop repository with `PYTHONDONTWRITEBYTECODE=1`:

```powershell
python -B -u -m research.rl_trading.v6.run_channel_underfit `
  --underfit D:/TradingML/runtimes/rl-v6-ranked-normalized-underfit-20261006 `
  --normalization-source-dir D:/TradingML/runtimes/rl-v6-bias-goal-20261006/normalizer-producer-97bcfdd05 `
  --selection D:/TradingML/runtimes/rl-v6-bias-goal-20261006/multisession-underfit-selection.json `
  --initial-cache D:/TradingML/runtimes/rl-v6-ranked-generalization-v2-20261006 `
  --initial-source D:/TradingML/runtimes/rl-v6-ranked-underfit-v3-20261006/manifest.json `
  --additional-cache D:/TradingML/runtimes/rl-v6-ranked-multisession-train-cache-20261006 `
  --width 512 --epochs 400 --activation-checkpointing --history-microbatch 8 `
  --gpu-duty-cycle 0.5 --cpu-saved-tensors --clocks-per-chunk 32 `
  --output D:/TradingML/runtimes/v6-own-channel-overfit-20261011
```

Only one laptop GPU experiment should run at a time. The runner retains 4 GiB
GPU and 8 GiB host memory headroom and requires online per-label W&B reporting.
Success requires one checkpoint with all current/future class F1 >= 0.95,
all supervised sizing/quality MAE <= 0.02, complete coverage, and exact reload
replay. A balanced overfit pass is not evidence of generalization or a natural
class-population pass.

## Staged frozen-context fitting

`staged_heads` exposes the experimental equal-row head-fitting objective and
an explicit parameter freeze. It updates current ENTRY/EXIT/quality, allocation,
separate forecast heads and the forecast GRU while retaining encoder parameters.
The input contexts must come from authenticated causal TRAIN replay; this module
does not authorize arbitrary saved tensors or development data. Its fitting loss
uses equal selected-row mass, whereas acceptance keeps the existing weighted
allocation/quality metrics and all five forecast gates. Missing allocation is
NaN and is supervised only for selected ENTRY rows.

The bounded October 11 experiment passed the balanced 128-target gate through
two exact six-session full-pipeline replays with one checkpoint. This establishes
underfit capacity, not natural-population accuracy or generalization. The shared
execution GRU is updated by forecast supervision, so behavior with actual order
outcomes still requires separate evaluation. A reusable authenticated staged
launcher is not yet integrated; the tensor objective alone is not an end-to-end
training command.
