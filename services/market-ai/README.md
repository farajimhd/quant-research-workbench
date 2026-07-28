# Market AI Service

Market AI is the slow contextual-hypothesis boundary. It receives a validated
news semantic label, freezes point-in-time QMD/market/SEC/fundamental context,
and invokes the Model Gateway route `news.trade_hypothesis.v2`.

The V2 contextual contract adds the latest three causal same-ticker news items
and only the reaction horizons that were fully observable before the current
article. It returns fixed-horizon probability and return hypotheses instead of
a single selected horizon.

It persists versioned, expiring structured hypotheses to
`q_live.news_market_hypothesis_v1`. It never places orders, chooses position
size, or overrides the strategy/risk runtime.

Default bind: `127.0.0.1:8803`.

```powershell
.\scripts\run_market_ai.ps1
```

The prior compact-event batching modules remain available as model-serving
primitives; the HTTP service is now the operational contextual inference path.
