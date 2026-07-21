# Canonical trading performance journal

Status: implemented schema v1
Scope: live, paper, replay, backtest, and backtest debug

## Purpose

The performance journal answers three different questions without conflating their data:

1. **What happened?** Immutable broker executions and orders remain the evidence.
2. **Was the trading decision profitable?** Flat-to-flat trade episodes are the reporting unit.
3. **Why did it happen?** Strategy revision, setup, exit reason, and durable review annotations provide attribution.

The operational Run Journal remains a separate debugging surface for events, checkpoints, and failures. It is not a performance report.

## Episode contract

A trade episode starts when an account and instrument move from flat to a non-zero position and closes when that position returns to flat. Scale-ins and partial exits stay inside the same episode. A reversal closes the old episode and starts a new episode with the unmatched quantity.

This definition is consistent across modes:

- live and paper derive episodes causally from canonical executions;
- replay uses the same simulated-broker executions;
- completed backtests adapt their existing flat-to-flat trade artifacts;
- FIFO `RoundTripTrade` rows remain an audit representation and do not determine journal win rate.

The `TradeEpisode` contract carries account, instrument, side, peak quantity, average entry and exit, gross and net P&L, fees, strategy id and revision, run, setup, exit reason, planned risk, MAE/MFE when available, and the exact execution and order identities.

## Metrics

| Metric | Calculation | Correct reading |
| --- | --- | --- |
| Win rate | profitable episodes / all closed episodes | Frequency only; read with payoff and expectancy |
| Payoff ratio | average win / average absolute loss | Magnitude asymmetry between winners and losers |
| Profit factor | gross winning P&L / gross losing P&L | Values above one indicate positive realized gross edge in the selected sample |
| Expectancy | win rate × average win − loss rate × average loss | Average net result per closed episode |
| Maximum drawdown | largest peak-to-trough decline in cumulative closed-episode net P&L | Realized path risk; it excludes open-position mark-to-market excursions |
| R multiple | net P&L / planned risk | Available only when planned risk was captured before entry |
| MAE / MFE | adverse / favorable episode excursion | Available only when the execution engine or completed backtest recorded the path evidence |
| Slippage | signed fill difference from signal price or arrival midpoint | Positive means worse execution for both buy and sell orders |

Missing values remain missing and coverage is shown explicitly. The report never replaces unavailable risk, excursion, or slippage evidence with zero.

## Strategy attribution

Reports group by `(strategy_id, strategy_revision)`. Revisions are intentionally not merged because doing so can hide a regression after a strategy change. Runs and setups remain searchable on each episode. Unattributed broker fills stay visible as `Unattributed` rather than being assigned by inference.

The simulator copies canonical attribution from the order metadata into every execution. The IBKR normalizer accepts the same canonical metadata at the broker boundary. This keeps strategy code independent from IBKR response shapes.

## Persistence and APIs

- `q_live.tr_trade_episode_v1` is the durable episode projection. The identifier is a string so deterministic UUIDs and externally supplied backtest identities are both lossless.
- `GET /api/trading/journal/report` returns the mode-consistent report for the requested account or run scope.
- `GET /api/trading/journal/episodes/{episode_id}/annotation` reads the review note.
- `PUT /api/trading/journal/episodes/{episode_id}/annotation` upserts review status, setup override, tags, and note in the crash-safe runtime journal.

Broker events and executions remain the reconstruction authority. The episode table is a deterministic reporting projection and may be rebuilt.

## Canvas information architecture

The **Trading Journal** container provides five focused views:

1. **Overview** — net P&L trajectory, expectancy, profit factor, win rate, payoff, drawdown, and edge diagnostics.
2. **Strategies** — revision-separated comparison with sample size, expectancy, and risk statistics.
3. **Trades** — searchable, sortable, filterable episodes with expandable evidence and review notes.
4. **Execution** — fill and order counts, fee state, signal/arrival slippage coverage, and venue concentration.
5. **Risk** — streaks, duration, planned-risk coverage, R multiple, MAE, and MFE.

The header always states the source scope and episode definition. Semantic green and red are reserved for beneficial and adverse results; neutral or unavailable evidence remains theme-neutral. Compact layouts keep fit-content tables horizontally scrollable instead of compressing numerical fields into unreadable columns.

## Limitations

- IBKR Client Portal trade history is bounded to the current day plus six previous days. Durable episode persistence is therefore required for a long-horizon live journal.
- Closed-episode equity is not an account-equity curve and excludes deposits, withdrawals, open risk, and FX translation.
- Strategy attribution is reliable only when the originating order carries canonical metadata.
- MAE, MFE, planned risk, and slippage are decision-grade only where their coverage indicators are complete enough for the selected sample.
