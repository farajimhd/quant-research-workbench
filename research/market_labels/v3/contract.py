VERSION = 'v66-hindsight-execution-spread-sidecar-v3'
SOURCE_SHA = 'f3357d530cdf6ba8b53d4e559c6b219dc3ebeda172a728cead5c5296c3749543'
SCOPE = dict(market='V66 sealed premarket ticker union', clocks='America/New_York candle closes',
    quotes='Certified execution liquidity; causal standing book; no age cutoff; timestamped invalid or zero-size updates replace prior books',
    scoring='(discounted(target bid - entry ask) - round-trip fees) / (entry ask + entry fee)',
    targets='Preserved 1a clocks; retained ENTRY requires its original target to be an original 1a EXIT',
    status='review_only_not_optimizer_approved')
