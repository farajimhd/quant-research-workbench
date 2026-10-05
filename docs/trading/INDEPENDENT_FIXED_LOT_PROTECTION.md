# Independent fixed-lot protection

Any strategy may declare `ProtectionProfile.add_policy =
AddProtectionPolicy.INDEPENDENT_FIXED_LOTS`. Profile identity and strategy
number do not select execution behavior. The existing typed intent field
`protection_add_policy` preserves the rule across normalized journal recovery;
no additional table, column or producer is required for this rule.

The initial supported acquisition is `enter_long`, with 2–32 uniquely owned
lots whose fractions total one. Every lot declares its own finite fixed stop
and target, with no inherited target or trailing rule. Unsupported acquisition
actions and incomplete rules fail before broker submission. Portfolio still
approves one aggregate quantity and reservation; the native planner divides
that approved quantity into owned brackets. These are independently protected
lots within one account position, not independent capital allocations.

Shared OMS reconciliation preserves each lot's cumulative entry and exit
fills. A partially acquired lot receives protection for its own remaining
shares; repair pairs retain the original lot identity and target. Persisted
commands precede dispatch. Partial acknowledgements remain outcome-unknown
until exact broker identities can be recovered, without duplicate submission.
Original brackets retire temporary repairs when their coverage becomes active.

Existing protection policies and their serialized payloads remain unchanged.
The prepared squeeze helper now declares the generic rule. Compatibility
imports retain the old helper API, but shared OMS dispatch has no profile-name
or strategy-number condition for this capability.

Validation covers real simulated-broker partial fills, repair retirement and
journal restart under two unrelated profile identities, normalized intent
round-trip, four-lot planning and invalid policy rejection. A local alternating
seven-repeat, 3,000-plans-per-repeat comparison against the pre-change planner
measured median existing-profile planning at 33.94 versus 33.89 microseconds,
and independent-lot planning at 33.63 versus 34.05 microseconds. This is an
order-planning microbenchmark, not a full-session throughput guarantee.
No market projection, vectorized candidate scan or per-ticker source query was
added. Validation occurs when constructing a profile; dispatch compares one
existing policy field at order lifecycle boundaries. Reconciliation remains
bounded by a group's owned lots and broker orders.

This change does not publish a numbered strategy or enable the unfinished
ladder evidence writer. Session admission locks, watchlist qualification and
cash rotation remain separate strategy/Portfolio rules; they must not be
inferred from this protection policy. Full native ladder publication and
financial session qualification remain outstanding.
