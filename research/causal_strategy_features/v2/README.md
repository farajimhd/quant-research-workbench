# Causal strategy feature research v2

Retains v1 and adds returns/range measured in strictly prior return standard
deviations, baseline availability and bar duration. The lookback and timeframe
remain parameters. Population standard deviation excludes the current bar.
Every trailing return must be known and the trailing bars must be contiguous.
Zero volatility and missing observations yield null normalized channels.

This remains development research, not a certified producer or strategy.
The v1 source, session isolation and availability requirements still apply.
Split/fundamental fields are unavailable until point-in-time coverage is
verified. No source candles are adjusted or invented here.
