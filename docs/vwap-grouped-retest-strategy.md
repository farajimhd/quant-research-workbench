# VWAP midpoint with grouped resistances and retest stops

Candidate 314 (`15c3410c-698a-4668-ac72-aa34ab91b7b8`) is a backtest-only
successor to Candidate 313. Its profile is `vwap-midpoint-grouped-retests-v2`.
The baseline remains available. All three new `vwap_ladder` switches default to
zero for existing configurations: `group_resistances`, `require_late_retest`,
and `allow_retest_stop_fallback`.

## Causal zones

The strategy groups resistance bands, without changing the V7 book. A zone's
lower and upper bounds are the union of its member bands. Neighboring bands
merge when their edge-to-edge gap is at most half the median positive gap
between previously broken zones. Until three positive historical gaps exist,
only overlapping bands merge. The current break cannot set its own threshold.
Membership and bounds freeze on the first price encounter. A recross counts
once; a newly discovered zone behind price cannot earn a retrospective break.

Grouped breaks use native completed 100ms closes, giving passive history and
active assignments the same zone identities and break counts. Stops and exits
still act on the executable event stream. Consequently, a crossing that reverses
within a 100ms candle does not advance the grouped ladder.

Zones replace physical resistance counts for adds, target distances, the six-break
late-entry regime, and stop progression. Early trailing starts on the third
zone break, below the first zone's lower band; subsequent stops remain two
broken zones behind. Existing late-entry trailing and target-move limits remain.

## Initial protection and late entries

Before six session zone breaks, prefer the existing confirmed support swing,
including a confirmed undercut and recovery. Its pivot may precede the MACD
episode. If none qualifies, require a previously broken resistance or transition
band, a later pivot candle intersecting that band, and a subsequent completed
1s close above its upper band. The pivot must be a confirmed active support
swing. Among qualified retests, choose the closest lower band below price.
Place the stop one configured tick offset below that lower band, rounded down.
Without qualifying evidence, skip entry rather than using an unanchored low.

At six or more session zone breaks, require that same confirmed pullback and
recovery after the late regime begins, even if an older support swing exists.
Any subsequent zone break invalidates earlier retest evidence. A break, retest,
and recovery cannot all be inferred from one candle. Detector resets clear
retest evidence, and all operands must be available at decision time.

The VWAP–HOD midpoint, five MACDs, one initial entry per MACD10s episode,
pre-04:05 ET exclusion, initial one-third cash allocation, and at most two adds
using then-available unreserved cash remain unchanged. The existing filtered
V7 seed gate remains mandatory; SUGP validation does not certify other tickers.

## Validation

`tests/test_resistance_zones.py` covers causal grouping, frozen membership,
recrosses, no retrospective breaks, late-entry rejection, fallback protection,
closest qualifying anchors, retest expiry, grouped adds/trailing, JSON state
round trips, and immutable candidate cloning. Run it with the existing ladder,
swing continuity, structural recovery, structural thesis, and coverage tests.
Historical acceptance must inspect actual entry anchors, orders, fills,
protection changes, and terminal reconciliation; unit results alone are not
historical or live acceptance.
