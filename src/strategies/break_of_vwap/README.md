# Break of VWAP

Break of VWAP is a low-turnover intraday long strategy that waits for a liquid
stock to reclaim VWAP from below. It is not a generic momentum scanner. The
core setup is a completed 1-minute bar that closes back above VWAP after the
symbol was below VWAP on the prior completed bar.

Implementation details are versioned under the version folders.
