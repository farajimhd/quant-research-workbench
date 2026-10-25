# V49 two-phase distributed optimization

24 certified training sessions; 12 later chronological validation sessions, display only.
Population 1024, seed 2020, original initial 6144 candidates per session.
Phase1: 50 generations across all tickers in each session, unrestricted session-score selection.
Phase2: 100 generations; sum the same cost across the 24 complete training sessions.

Score = NetProfit - 0.5 * GrossLosingPositionDollars - 0.2 * EquityDrawdown.
EquityDrawdown is the existing replay mark-to-market maximum drawdown, including open positions.
Global drawdown penalty is the sum of per-session maximum equity drawdowns; it is not labelled
as one continuous cross-session maximum drawdown. No ticker quotas, no phase0, no specialist promotion.
Keep V48 bid/ask fills, fees, risk limits, whole-network and ordinary crossover,
mutation, ancestry/species, elite/fresh allocations and bounded caches/reserves.
Historical positive session archives seed global evolution with 75% experts and 25% fresh programs.
Validation occurs after the successor and training RNG are frozen and hash-verified.

Deploy immutable committed code; use scheduled_launch for app-independent processes.
full_progress supports phase1 session detail and global training/validation scores with color bars.
Preserve V48 checkpoints separately. Never change their source or objective contracts.
