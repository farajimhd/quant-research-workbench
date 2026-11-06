VERSION = 'v66-hindsight-spread-sidecar-v2'
SOURCE_SHA = 'f3357d530cdf6ba8b53d4e559c6b219dc3ebeda172a728cead5c5296c3749543'
SCOPE = dict(market='V66 sealed premarket ticker union', clocks='America/New_York candle closes',
    quotes='Exact completed 1s bid/ask bar closes; no carry across absent bars',
    scoring='(discounted(target bid - entry ask) - round-trip fees) / (entry ask + entry fee)',
    targets='Preserved 1a clocks; retained ENTRY requires its original target to be an original 1a EXIT',
    status='review_only_not_optimizer_approved')
