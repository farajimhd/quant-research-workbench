# Dated Backtest input exclusions

An operator may explicitly exclude a listing whose required structural inputs
are unavailable. This is a scope change, not a coverage certificate. Producers
and new Backtest preflight share `src/backend/backtest_input_scope.py`.

The operator-owned policy is
`D:\TradingML\runtimes\backtest-preparation\input-exclusions.json`, or the exact
path in `BACKTEST_INPUT_EXCLUSIONS_FILE`. Each row contains `ticker`, ISO `start`
and `end`, and a nonempty `reason`. Missing policy means no exclusions. Invalid
policy fails closed. Preserve the policy for retained scoped-run review.

All source candidate coverage is certified before removing the explicitly
excluded ticker from execution and derivative coverage scopes. Parent bars,
indicators, liquidity, candidate rows, and structural products remain intact.
The projected candidate token includes the exclusion list. Preflight reports
the symbols, and the launch market pins retain that list. Execution reconstructs
the pinned list rather than reading today's policy. Other missing tickers still
fail certification.

Saved charts first reconstruct the original full candidate scope. An explicit
excluded scope is accepted only when its exact V7 seed token matches the
immutable run's token; missing coverage alone never proves a compatible scope.

The user authorized LGHL exclusion for July 30 through September 12, 2026,
because its structural levels are unavailable. It applies to shared input
preparation for the numbered fixed Backtest strategies; it does not change
entry, management, sizing, liquidation, or fill rules.
