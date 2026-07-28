# AI inference services

The live AI path uses three independent service authorities:

1. **Model Gateway (`:8802`)** executes named structured-inference routes.
   It owns provider profiles, fallback, timeouts, concurrency, idempotency,
   route budgets, usage, cost, and metadata-only audit state. It has no news or
   trading policy.
2. **News Intelligence (`:8804`)** owns news eligibility and semantic meaning.
   It accepts candidates only after News Gateway has durably published the
   canonical V2 rendered article. It operates only while the explicit live
   trading session gate is active, applies deterministic kind/scope filters,
   freezes the QMD ticker snapshot, validates the current
   `gpt_oss_news_semantics_v1` output, and persists it.
3. **Market AI (`:8803`)** owns deeper contextual hypotheses. It freezes the
   semantic label and point-in-time QMD, market, SEC, and fundamental context;
   invokes `news.trade_hypothesis.v2`; validates fixed-horizon probability
   coherence; and
   persists an expiring hypothesis.

News Gateway remains the canonical acquisition/rendering authority. QMD remains
the market-data authority. The strategy and shared trading runtime remain the
only decision/risk authorities; none of these three services can place orders.

## Live flow

```text
News Gateway canonical V2 publish
  -> News Intelligence bounded queue
     -> deterministic scope/kind + active-session + QMD price gate
     -> Model Gateway: news.semantic_fast.v1
     -> q_live.news_semantic_label_v1
     -> Market AI bounded queue
        -> frozen point-in-time context
        -> Model Gateway: news.trade_hypothesis.v2
        -> q_live.news_market_hypothesis_v1
        -> strategy may consume only before expires_at_utc
```

The notification from News Gateway is deliberately downstream and
non-authoritative: inference unavailability cannot roll back canonical news.
Model requests are idempotent by content/contract identity. Generated audit
state belongs under `D:\TradingML\runtimes`, never inside the repository.

## Start order

```powershell
.\scripts\run_model_gateway.ps1
.\scripts\run_market_ai.ps1
.\scripts\run_news_intelligence.ps1
.\scripts\run_news_gateway.ps1
```

Starting and stopping the backend live market gateway updates the explicit News
Intelligence session gate immediately. News Intelligence also polls the
backend market-gateway status, so it restores the gate after a service restart
and fails closed when that authority is unavailable. Verify
`GET http://127.0.0.1:8804/live-session` before expecting paid or local live
inference.

When services run on different machines, set the advertised dependencies
explicitly rather than relying on loopback defaults:

- `MODEL_GATEWAY_VLLM_URL`
- `NEWS_INTELLIGENCE_MODEL_GATEWAY_URL`
- `NEWS_INTELLIGENCE_MARKET_AI_URL`
- `NEWS_INTELLIGENCE_BACKEND_URL`
- `NEWS_INTELLIGENCE_QMD_URL`
- `MARKET_AI_MODEL_GATEWAY_URL`
- `MARKET_AI_BACKEND_URL` or `MARKET_AI_QMD_URL`
