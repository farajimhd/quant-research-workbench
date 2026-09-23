# Event identity, clocks, and storage

## Proposed compact bar contract

ARTE needs a versioned compact bar product, beginning with completed 100 ms
bars. This contract is not materialized yet. Define its exact schema and codecs
from measured workloads before enabling a writer. It must include instrument,
session, half-open interval, timeframe, adjustment and calculation versions,
source generation and coverage identity, completeness, OHLC, volume, trade count,
and the minimal fields required by Strategy 350 and its scanner, signal, and
level rules. Preserve exact price and size semantics and explicit empty buckets;
do not invent trades or forward-fill a price without a named rule.

Use columnar arrays and compact physical types where lossless. Measure
compression, range-query cost, and rebuild cost. Do not repeat run-wide metadata
on every bar. Materialize only products required for backtest, continuation, or
auditable decisions; set bounded retention and pin referenced generations.
Derived indicators and levels have separate versioned contracts and causal
availability. Bar aggregation alone cannot restore missing event-level fields.

## Agreed decisions

- Use one logical compact event authority for trades and quotes.
- Insert rows without a dense ordinal.
- Preserve source sequence, identity, precision, and conditions.
- Do not duplicate a payload just because both transports delivered it.
- Preserve what was known live when REST later supplies additional information.

## Logical fields

| Group | Fields and rules |
|---|---|
| Identity | Provider, stable instrument, provider session, event type, source sequence |
| Trade identity | Trade ID, exchange and TRF scope where supplied |
| Market payload | Exact price/size representation, bid/ask fields, conditions, correction data |
| Source time | SIP and participant timestamps; preserve native precision |
| Live observation | Receive UTC, monotonic offset within a run, lane/application sequence |
| Provenance | Codec version, source capability profile, publication or acquisition batch |

For a quote, participant time means quote generation time, not trade execution.
Absent participant or receive timestamps remain absent. No SIP fallback is evidence
that an event was timely. Store timestamps in a common unit with precision metadata.
Converting milliseconds to nanoseconds does not create nanosecond accuracy.

Run-level constants and batch metadata belong in referenced metadata records.
Do not repeat configuration JSON, provider names, or entire request bodies per event.
Use interned IDs where measured compression and lookup costs justify them.
Do not truncate condition arrays or fractional sizes to fit a legacy codec.

## Identity gate

Proposed source key:

`provider_id, instrument_id, provider_session, event_type, provider_sequence`

Validate this key on overlapping REST and WebSocket data before final DDL.
Test sequence resets, ticker changes, corrections, duplicate deliveries, and trades
with the same trade ID on different venues. Sequence jumps do not prove a gap.
Do not assume trades and quotes share one sequence domain.

If the key is insufficient, extend it with verified source identity fields.
Do not invent identity from payload equality. Two real trades may have equal payloads.

## Physical layout

Proposed partition: calendar month of the provider session date.
Proposed sort key: instrument, session, event type, provider sequence.
All key fields must be resolved before insertion. None requires future events.
The final key and codecs require workload and identity tests.

Insertion order is not query order. Replay must request its explicit ordering.
ClickHouse sorting keys do not enforce uniqueness. Idempotent writers and canonical
read resolution must handle duplicates before background merges finish.

## Minimal persistence model

- Store each normalized logical payload once in the event authority.
- Store live receipt metadata separately when required for recorded-live replay.
- Store REST-only enrichment as additional fields or a sparse extension.
- Store a new payload revision only for a real correction or conflict.
- Store source coverage once per certified interval.

The final physical layout must choose and test enrichment handling before writers
are enabled. A destructive latest-only overwrite is forbidden. A live audit must
still resolve the payload and fields available at its recorded decision boundary.
Transport overlap alone must not double storage or event counts.

## Three distinct orders

| Order | Purpose | Persistence rule |
|---|---|---|
| Source identity/order | Match, reconcile and retrieve events | Available on receipt; not dense |
| Historical replay order | Deterministic merged trade/quote replay | Versioned source-clock and tie-break contract |
| Live application order | Reproduce observations delivered to the engine | Run, lane and monotonic application sequence |

Historical ordering uses SIP availability and verified source ordering. Participant
time can affect eligibility or chart geometry, but cannot move a late report into
the earlier decision history. Cross-channel ties need a documented deterministic
rule; deterministic does not imply known exchange causality.

Dense array positions may exist inside an immutable prepared backtest generation.
They are not event IDs or event-table keys. Checkpoints pin generation plus position.
Repair publishes a successor generation instead of renumbering an active run.

## Revision and knowledge time

Historical replay pins a certified reconciled generation. Recorded-live replay pins
original observations and their input references. Late REST enrichment cannot appear
in an earlier live decision. Repeated receipts can be counted without copying payloads.
REST response time is acquisition time, not original market receive time.
