# VWAP ladder entry confirmation

The impulse-qualified pullback profile keeps a confirmed, recovered swing
eligible while awaiting its 1s MACD confirmation. It does not expire after one
second. The swing must remain active, match its level-retest witness, belong to
an unconsumed and non-invalidated impulse, and meet the 20%-45% retracement rule.
Current price, VWAP, executable quotes, stop and target geometry are checked at
entry. Candidate swings are filtered for eligibility before selecting the best
level anchor. Older profiles without impulse qualification retain their existing
fresh-recovery requirement.

A post-move breakout requires an observed below-to-above crossing of the highest
resistance below HOD plus the configured offset. The crossing is retained in
checkpointed strategy state while indicator confirmation is pending. It is
invalidated when price returns below the trigger, reaches the original target,
the reference level identity or band changes, or the 10s episode ends or is
consumed. An already-above price alone cannot create a crossing. All entry
conditions and executable protection geometry are rechecked before submission.

Replay's event-native forming 1s MACD is marked `sample_kind=forming`. Its
completed counterpart is preserved under the source key suffixed `:completed`.
The VWAP ladder uses completed native samples with their original timestamps;
forming previews cannot overwrite or masquerade as completed evidence. Other
strategies retain their existing preview behavior. Using forming confirmation
for this strategy is a separate policy choice, not a timestamp-validation bypass.
