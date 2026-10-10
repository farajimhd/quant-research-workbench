# V6 Torch replay and optimization

See [the V6 README](../README.md) for the current data authority, compact replay,
six lifecycle programs, lower-tail objective, profiling and launch constraints.

V5 staged-campaign objectives and inherited profiling are not V6 qualification.
Full optimization requires the user's final parameter decision. Sealed
validation is outside the current implementation and profiling scope.

`run_full_search --history <completed-history-root>` binds certified offline
broker histories and row lookup maps. It bypasses online swing/window reductions
while preserving financial fills, orders, cash and position behavior. Omitting
`--history` retains the original online-history path. Histories use the agreed
1–60-second windows and swing ladder. Input, history, and algorithm hashes bind
the resume contract; earlier immutable campaigns are not resumed with this code.

The complete history is larger than GPU memory. History-enabled replay loads
bounded session cohorts and retains the all-30-session parent-selection barrier.
V7 is a separate assumed-fill position evaluator; it does not replace this broker.
