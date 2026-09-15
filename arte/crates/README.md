# Shared Rust libraries

Planned modules: contracts, market normalization, bars, indicators, historical V7,
streaming V7, detectors, strategy, portfolio, OMS, replay, and persistence interfaces.

Domain libraries do not import UI code. Share semantics between executable modes.
No crate is implemented yet. The future Cargo workspace must be rooted here or at
this project's root, never in its temporary parent repository.
