# QMD Gateway Documentation

This folder is the review package for `qmd-gateway`.

Use it to comment on the market-data gateway without mixing in app-backend topics. The gateway handles Massive live quotes/trades, bars, indicators, scanner primitives, raw persistence, gap fill, replay, and operational metrics. It does not handle IBKR, portfolios, final trading orders, reference joins, chart history merge, or UI session state.

## Review Order

1. [ARCHITECTURE.md](ARCHITECTURE.md)
   - Read first. It defines the system boundary, module ownership, runtime flow, and what belongs outside the gateway.
2. [CONFIGURATION.md](CONFIGURATION.md)
   - Every environment variable, default value, effect, and tuning note.
3. [DATA_CONTRACTS.md](DATA_CONTRACTS.md)
   - Raw trade/quote rows, bar fields, tick indicators, bar indicators, formulas, and persistence rules.
4. [SCANNER_AND_SIGNALS.md](SCANNER_AND_SIGNALS.md)
   - Current Massive-only scanner primitives and the signal-method catalog contracts.
5. [OPERATIONS.md](OPERATIONS.md)
   - Gap fill, replay, metrics, backpressure, failure behavior, and review checklist.

## Terms Used In These Docs

- **Gateway**: the Rust process under `services/qmd-gateway`.
- **Massive-only**: uses only Massive quotes, trades, and values derived from them. No broker or reference data.
- **NBBO**: National Best Bid and Offer, represented here by Massive quote bid/ask fields. This is not level 2 order book depth.
- **Bar**: aggregated quote/trade data for a fixed timeframe such as `1s`, `1m`, or `1h`.
- **Indicator**: reusable computed state such as EMA, RSI, spread, trade rate, or tape imbalance.
- **Scanner primitive**: an early market-data candidate emitted by the gateway. It is not a final trade signal.
- **Signal method**: a cataloged trading setup contract. Most are not implemented as gateway primitives yet.
- **Hot path**: code that runs while live Massive data is arriving. It must avoid blocking.
- **Backpressure**: queues filling faster than consumers can process them. Required data paths wait for capacity; UI broadcasts remain best effort.
