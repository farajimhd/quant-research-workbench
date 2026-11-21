# V18 corrective qualification for the authorized20-epoch full run

V17's eight-arm campaign completed. Full validation improved98/108 all-visible
raw groups, but the price-preserving candidate retained a masked daily ask-count
MAE of3.55e9 and maximum error9.59e16. It is not promoted. User subsequently
authorized20 full epochs once a supported candidate is found. This follow-up is
bounded to two matched15000-update arms, each under60minutes, before that launch.

Both arms retain the exact V17 architecture, frozen16D Phase0 teacher,256D learned
embedding, decoder, dataset identities, calibration and16/64 visibility schedule.
No input data, references, directions or summaries are appended to embeddings.
Both start from V17's price-preserving checkpoint, with fresh AdamW1e-5 and seed559.

The corrective joint objective retains body coefficient0.5 and price0.01. It
supervises activity log levels on visible AND hidden source-valid bars (weights1
and0.1), while activity-change supervision uses visible adjacent pairs only.
Activity level loss uses pre-clamp coordinates, so negative coordinates retain
correction gradients; inference outputs and existing Phase0 constraints are
unchanged. A0.01 fourth-power log-error term penalizes large errors without
clipping valid targets or imposing an output ceiling. These changes jointly
address the confirmed hidden-scale supervision gap and exponential error tails.

`joint_calibrated` differs only by dividing change errors by a training-only
RMS log-change per timeframe/channel. Statistics are fitted to the frozen
training subset only, recorded in each manifest, and never supplied at inference.
Constant groups use scale1, with explicit counts. Training up/down directions are
approximately balanced; quote-size changes have lower mean-square log magnitude
and more flat pairs than volume, so equal log-MSE does not equalize these tasks.
That is motivation for this ablation, not proof of the remaining defect's cause.

Selection requires raw scale/tail improvement and retention of both price and
activity information. Keep all-visible and masked metrics separate. Full training
remains conditional on qualification, with online W&B and the workstation panel.
