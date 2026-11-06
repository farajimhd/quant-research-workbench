"""Review-only label product identity; safe to import without compute libraries."""
VERSION = 'v66-hindsight-sidecar-v1'
SCOPE = dict(market='V66 sealed premarket ticker union', clocks='04:00:01 through 09:30:00 America/New_York',
    macd='V66 12/26/9 session-reset float32 relative outputs decoded with raw close',
    grouping='All stored session-union tickers; scanner membership retained separately',
    horizon='Stored premarket only; final pairs flagged right_censored, no post-cutoff prices',
    timing='Labels attach to candle close; optimizer alignment not integrated',
    status='review_only_not_optimizer_approved')
