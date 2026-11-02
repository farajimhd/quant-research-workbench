# V64 contract

V64 starts 6,144 fresh founders with 1–2 reachable nodes per action (5–10 total),
then evolves populations of 1,024. Evolved limits remain 1,024 total/per-action
nodes. The global campaign uses 24 training and 12 display-only validation
sessions, 200 generations and six workstation lanes with 256-clock event-based
transfers. See WHOLE_SHARE_REQUESTS.md for fixed entry sizing and the five-add
strategy request boundary.

Inference consumes the first five entries of the existing saved ranking at each
clock. All resolutions use the same five listing identities. Materialized model
input blocks contain only the population's requested features. Networks have no
position, order, fill or recurrent feedback. Crossing operators read the prior
market observation of the same ticker, not the prior occupant of its rank slot.

Candidate price programs produce concrete buy/sell limits, initial stop, target
and stop-update prices. They can reference quotes, completed candle extrema,
previous completed candle extrema, rolling extrema (5 through 60 seconds in
steps of 5), and causally confirmed swing extrema. Explicit min/max/blend and
availability branches belong to the genome. Missing references do not receive
an implicit stop or price replacement. Source shards are unchanged; derived
references exist only in bounded session memory, and no database or downloader
is used. Legacy policy scalars are canonical ABI placeholders, not evolved genes.

Execution reads only concrete signals/prices and quote/mark/size records. Its
active set is the current five tickers plus outstanding orders and holdings.
Rank loss cancels outstanding buys and requests marketable liquidation until
flat. Terminal and candidate stop/target orders are also deterministic execution
obligations. Partial fills consume each displayed quote size once per snapshot;
sells cannot exceed holdings. There is a three-second delay after becoming flat
before a new entry. Existing execution-tail coverage must suffice; otherwise
the replay fails visibly and publishes no fitness.

Fitness retains the existing logarithmic ticker/session rewards, subtracting
$5 per distinct blocked request. Drawdown is diagnostic only. An outstanding order is
charged at most once, not on every waiting clock. Partial fills and rank loss
alone carry no penalty. The strategy request adapter suppresses exit/reduce
signals while flat before submission, without neural position feedback. They
cannot cancel an unfilled entry or accrue blocked-order penalties. Validation
cannot select parents or tune fitness.

GPU inference overlaps native CPU execution through a bounded pipeline. Order
pages are journaled asynchronously; publication backpressure is measured.
Session I/O uses the existing bounded prefetch lanes. Live panel snapshots do
not regress to an earlier generation within an owner lifetime, and historical
drill-downs remain separate from live status.

Qualification must cover exact numerical/order parity for implementation-only
optimizations, fresh-node and creation boundaries, two concurrent sessions,
reproduction concurrent with validation, publication, checkpoint/resume and
panel data loading. The target is total elapsed time divided by completed
1,024-candidate sessions below 35 seconds, including overhead; cold
load, compilation, preparation and publication times must also be reported.
