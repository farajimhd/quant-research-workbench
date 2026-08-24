# News Hypothesis Service

News Hypothesis is the slow contextual-hypothesis boundary. It receives a validated
news semantic label, freezes point-in-time QMD/market/SEC/fundamental context,
and invokes the Model Gateway route `news.trade_hypothesis.v2`.

The V2 contextual contract adds the latest three causal same-ticker news items
and only the reaction horizons that were fully observable before the current
article. It returns fixed-horizon probability and return hypotheses instead of
a single selected horizon.

It persists versioned, expiring structured hypotheses to
`q_live.news_market_hypothesis_v1` and every completed prediction to the
append-only `q_live.news_market_hypothesis_history_v1`. It never places orders,
chooses position size, or overrides the strategy/risk runtime.

The context is causal at the article publication timestamp: bounded session
price summaries, point-in-time fundamentals, recent SEC filing metadata and
available SEC labels, and prior same-ticker News. Unavailable QMD or SEC inputs
are represented explicitly rather than replaced by current or future data.

`NEWS_HYPOTHESIS_TRIGGER_MODE` defaults to `manual`. In manual mode the service
accepts explicit `/hypothesize` requests from a user-triggered issuer review but
does not reconcile and generate predictions on its own. Set it to `automatic`
only together with the Text Intelligence automatic-review switch when the
automatic path is intentionally enabled.

Default bind: `127.0.0.1:8803`.

```powershell
.\scripts\run_news_hypothesis.ps1
```

This service has no market-model serving or order authority. BarGPT is served
by the independent BarGPT service.
