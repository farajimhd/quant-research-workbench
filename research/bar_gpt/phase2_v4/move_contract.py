"""CPU/GPU-independent signed move-class definitions."""
CHANNELS=('trade_close','bid_close','ask_close')
EDGES_PCT=(.05,.1,.25,.5)
RANGES=('0_to_0.05pct','0.05_to_0.1pct','0.1_to_0.25pct','0.25_to_0.5pct','above_0.5pct')
BINS=('unchanged',)+tuple(f'{side}_{band}' for side in ('down','up') for band in RANGES)
