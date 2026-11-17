# Lightweight decoder reconstruction campaign

V7 preserves V6 experiments and their exposed audit. A new immutable audit excludes
every source-job group previously audited; train, development and tiny sets are
byte-identical to the prior corrected experiment subset. Runtime root:
`D:/TradingML/runtimes/bar_gpt/encoder-campaign2-20261117`.

The bounded campaign allows eight identities, eight hours total and sixty minutes
per identity including profiling/evaluation. No full-dataset training is authorized.

The exported vector contains eighteen visible-input scale coordinates, three quote
validity fractions and learned coordinates. A residual dense encoder sees only
supplied bars and their positions/validity/time features. The decoder is one affine
map followed by fixed algebra: open and body in log units; squared upper/lower
wick roots enforce OHLC; squared activity roots enforce nonnegative log1p activity.
There is no sorting, clipping, hidden reference, target statistic or input bypass.
All scale coordinates are part of the fixed-size exported vector.

Canonical input uses centered log prices and centered sqrt(log1p activity).
Geometry input uses log open, signed log body and square-root log wick lengths.
Partial OHLC bars retain every valid canonical input; added geometry features
are available only for complete OHLC groups. Complete source ordering violations
fail qualification and are never silently repaired. Prices use percent-like log units in the loss,
activity uses log1p units; valid channel means and price/activity families receive
equal weight. Fixed input units are 100 for price displacement/body and 10 for wick
roots; these are design constants, not fitted target metadata.

Calibration remains training-only. Sampling gives equal mass to observed
timeframe/last-close-price-band cells; calibration and metrics are unweighted.
Selection is 50 times balanced price-relative mean plus activity log MAE plus
0.05 times worst-band relative mean. Every group, including rare groups, remains
reported. Structural violations and nonfinites are hard failures.

Initial hypothesis: all-visible training with a stronger residual encoder and a
single linear structured decoder improves input reconstruction and fixes the prior
independent-head OHLC defect. Planned ablations vary input coordinates, bottleneck,
encoder capacity and masking; conclusions and exact identities live in the runtime
ledger. All-visible and masked reconstruction are separately reported. Masked
evaluation is a stress test for all-visible-trained candidates.

The final-slot `geometry_joint` revision addresses observed crossed quote outputs.
Training source inspection found zero bid/ask crossings for each corresponding
OHLC field. Four additional visible paired-quote root coordinates are exported
inside the same embedding; centered paired coordinates and validity join the
encoder input. There are 25 explicit coordinates and the remainder are learned.
The single affine head predicts residual roots. Ask open/close are constructed
above bid open/close, ask high above both ask body and bid high, and ask low between
bid low and ask body. These equations enforce OHLC and paired-quote ordering by
parameterization, with no post-hoc sorting or clipping of predictions or targets.
Source crossed quotes fail closed; this model is inapplicable to a source contract
that permits valid crossings. Partial quote fields remain in canonical inputs.
This coordinated input/output revision replaces the planned seed replication;
seed stability is therefore not established by this campaign.

Run `python -B -m unittest research.bar_gpt.phase1_v7.test_contract` with
`PYTHONDONTWRITEBYTECODE=1`. Use the versioned launcher for an immutable deployment;
it requires dataset and campaign receipts and enforces bounded identities.

## Completed bounded comparison

Eight attempt identities were used; seven completed 12,000 updates. E02 stopped
before training because a model-only structural acceptance check was accidentally
applied to the median control. Its evidence is retained; E03 repeated the geometry
hypothesis after correcting the check. No additional candidate was launched.

| Attempt | Change | Dev price relative mean | Dev activity log MAE |
|---|---|---:|---:|
| E01 | canonical, width 1024, embedding 512 | 0.804% | 0.458 |
| E03 | add candle geometry | 0.738% | 0.445 |
| E04 | embedding 256 | 0.765% | 0.501 |
| E05 | embedding 768 | 0.745% | 0.407 |
| E06 | encoder width 2048 | 0.728% | 0.265 |
| E07 | 10% masked batches | 0.753% | 0.419 |
| E08 | coupled quote input/output geometry | 0.734% | 0.260 |

E08 is the recommended **all-visible reconstruction research candidate**. Its
encoder has width 2048 and three residual blocks, a 768-coordinate export, and
743 learned coordinates alongside 25 explicit visible-input coordinates. Total
parameters are 40,449,895; the one-affine decoder has 885,888 parameters. There is
no feature dropout, all-visible training probability is one, batch 512, seed 17,
AdamW learning rate 3e-4 with cosine decay to 3e-5, weight decay .01 and gradient
norm limit one. Encoder and readout are FP32, inverse exponentiation FP64.

The fresh audit has 6,072 windows, excluding every previously audited source-job
group. E08 all-visible balanced price relative mean is 0.00895621 and activity
log1p MAE is 0.282765. There are zero OHLC violations among 1,165,432 eligible
side-bars, zero crossed closes among 388,412 eligible quote pairs, zero negative
outputs and zero nonfinites. The old V6 recommended checkpoint evaluated on this
same audit yields 0.0235916 and 0.552967 respectively. This is a comparison of
complete candidates, not a transformation-only or compute-matched ablation.

The independent-quote E06 comparator has slightly better audit price error (0.00882606)
and worst-band mean (0.154039 versus E08 0.167328), but 87,894 crossed closes.
E08 is preferred for its coupled valid outputs with similar reconstruction.
The worst E08 audit band is daily ask-high below $0.10 with only two observations;
the separate development bid-low outlier still has approximately 534% error.
Audit coverage has only 68 daily windows and no below-$1 one-second windows.

Fixed-budget nonlinear frozen-encoder probes improve audit return/range/volatility
R2 over E08's untrained encoder: .898/.857/.757 versus .825/.821/.739. Spread R2
regresses (.677 versus .835). Linear probes and all MAEs are also retained. Direct
exported log-price scale MAE is 1.60e-7; this is explicit structural preservation,
not a learned gain. Raw-summary controls remain stronger. No future task or
trading utility is established. Masked audit price error is 31.53%, so E08 is not
recommended for missing-bar reconstruction. E07's development masked error is
1.28%, with a material all-visible activity penalty; its coupled-quote combination
was not tested. Seed replication was not performed.

Pinned training source: `3c6eed1ac20d909201219e208bf28a2c5f0396ec`.
Dataset seal: `ae3fc4d4d19d18a4c3d4501ac53c81443871418775d51e2d29009f71747dc759`.
Checkpoint SHA256: `69d46de6c1ae6950292c90ae56f9f6cbe6619a11c1c772c8f4cbea65ae979dec`.
Runtime checkpoint: `finalist-audit/e08-coupled-quotes-pinned.pt`; the corresponding
`-encoder-only.pt` omits decoder and optimizer state. `reproduce` evaluates a pinned
checkpoint against the sealed development subset without opening the audit.

Historical manifest compatibility fields `decoder=256` and `validity_slots=false`
were inherited CLI defaults and did not control V7 architecture: every V7 model
always used one affine decoder and three validity slots. They remain unchanged
in immutable run records; current launcher defaults and architecture metadata
now state the actual implementation. Training mean/std statistics are retained
for controls; V7 model coordinates use the explicit visible references and fixed
unit multipliers, not global z-score normalization. E01's per-evaluation JSON
retained a legacy score field; its checkpoint selection and learning curve used
the declared V7 score, which the comparison recomputes from component metrics.

The current trainer caches the bounded subset. Full-dataset training requires a
qualified streaming path and full-source paired-quote contract verification. A
configuration proposal is recorded in runtime; no full-dataset run was launched.
