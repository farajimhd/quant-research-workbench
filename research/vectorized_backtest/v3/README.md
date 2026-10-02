# Vectorized backtest v3

V3 is a standalone copy of the required v2 source, with a semantic strategy
representation and a bounded genetic optimizer. V2 is not modified or imported.
The copied source adapters continue to use the shared certified ARTE and
canonical V7 services; they do not duplicate or replace those authorities.

Start with [the implementation guide](torch_backtest/README.md). The new entry
point is `research.vectorized_backtest.v3.torch_backtest.optimize`.

The v2 grid/qualification tools are retained for comparison. `run_grid` and
`run_workstation` still enumerate that fixed grid; they do **not** launch the
v3 genetic search. No v3 historical optimization is started on import or by
the default plan command.
