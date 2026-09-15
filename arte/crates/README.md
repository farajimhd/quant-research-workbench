# Shared Rust libraries

Planned modules: contracts, market normalization, bars, indicators, historical V7,
streaming V7, detectors, strategy, portfolio, OMS, replay, and persistence interfaces.

Domain libraries do not import UI code. Share semantics between executable modes.
`arte-core` implements events, coverage, bars, basic indicators, latency gates,
account reservations, brackets, order state, seed publication, parallel market replay,
setup primitives and V7 numerical primitives. Full V7 and the complete strategy remain
unimplemented. The Cargo workspace is rooted in ARTE.
