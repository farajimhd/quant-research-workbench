# AI inference services

The live AI path uses four independent service authorities:

1. **Model Gateway (`:8802`)** executes named structured-inference routes.
   It owns provider profiles, fallback, timeouts, concurrency, idempotency,
   route budgets, usage, cost, and metadata-only audit state. It has no news or
   trading policy.
2. **Text Intelligence (`:8804`)** owns informational News Synthesis V1, the
   separately versioned SEC semantics, DeepFM forecast-eligibility scoring, and
   issuer-review orchestration. Its reconciliation loops run independently of
   trading state after News Gateway or SEC Gateway durably publishes canonical
   rendered text. Every News revision receives both a synthesis view and a
   hash-pinned DeepFM score. Synthesis cannot gate DeepFM or participate in a
   forecast decision. It may participate only in the protected positive-direction
   plus DeepFM-eligible Signal Stream; synthesis-only signal rules fail closed.
   DeepFM is the sole live eligibility authority
   at the configured operating threshold. Explicit or enabled automatic issuer
   review invokes `news.issuer_review.v1` and persists validated current and
   append-only results.
3. **News Hypothesis (`:8803`)** owns deeper contextual hypotheses. It freezes the
   semantic label and point-in-time QMD, market, SEC, and fundamental context;
   invokes `news.trade_hypothesis.v2`; validates fixed-horizon probability
   coherence; and
   persists an expiring hypothesis.
4. **BarGPT (`:8805`)** owns causal BarGPT v2/v3 model serving. It warms
   mode/run-scoped context from QMD History, consumes live QMD compact events,
   batches full-prefix checkpoint inference, and publishes raw heads plus
   explicitly decoded Data Fields. It has no rule, strategy, risk, or order authority.

News Gateway remains the canonical acquisition/rendering authority. QMD remains
the market-data authority. The strategy and shared trading runtime remain the
only decision/risk authorities; none of these three services can place orders.

## Live flow

```text
News Gateway canonical V2 publish
  -> Text Intelligence bounded queue
     -> News Synthesis V1 persistence (informational view only)
     -> hash-pinned DeepFM score for every revision
     -> q_live.news_forecast_funnel_v1
     -> manual Review, or explicitly enabled automatic Review
        -> Model Gateway: news.issuer_review.v1
        -> q_live.news_llm_issuer_review_v1 + append-only history
     -> separate Forecast Reaction action, or enabled automatic handoff
        -> News Hypothesis bounded queue
        -> frozen point-in-time context
        -> Model Gateway: news.trade_hypothesis.v2
        -> q_live.news_market_hypothesis_v1 + append-only history
     -> configured Data Fields / Rule Sets / Signal Streams
```

The notification from News Gateway is deliberately downstream and
non-authoritative: inference unavailability cannot roll back canonical news.
Model requests are idempotent by content/contract identity. Manual Review does
not silently trigger Forecast Reaction. Automatic Review and Reaction remain
implemented but disabled by the production launchers. Generated audit state
belongs under `D:\TradingML\runtimes`, never inside the repository.

## Start order

```powershell
.\scripts\run_model_gateway.ps1
.\scripts\run_news_hypothesis.ps1
.\scripts\run_text_intelligence.ps1
.\scripts\run_news_gateway.ps1
.\scripts\run_bar_gpt.ps1
```

DeepFM and News Synthesis reconciliation do not depend on an active trading
session. The explicit live-session state controls automatic live forwarding and
context-sensitive legacy inference; manual Review remains an explicit request.
Text Intelligence polls backend status to restore the session state after a
restart and fails closed when required live context is unavailable. Verify
`GET http://127.0.0.1:8804/live-session` when diagnosing automatic live work.

When services run on different machines, set the advertised dependencies
explicitly rather than relying on loopback defaults:

- `MODEL_GATEWAY_VLLM_URL`
- `NEWS_INTELLIGENCE_MODEL_GATEWAY_URL`
- `NEWS_INTELLIGENCE_NEWS_HYPOTHESIS_URL`
- `NEWS_INTELLIGENCE_BACKEND_URL`
- `NEWS_INTELLIGENCE_QMD_URL`
- `NEWS_HYPOTHESIS_MODEL_GATEWAY_URL`
- `NEWS_HYPOTHESIS_BACKEND_URL` or `NEWS_HYPOTHESIS_QMD_URL`
- `BAR_GPT_RELEASES_JSON` or `BAR_GPT_V2_CHECKPOINT` / `BAR_GPT_V3_CHECKPOINT`
- `BAR_GPT_QMD_HTTP_URL`, `BAR_GPT_QMD_WS_URL`, and `BAR_GPT_BACKEND_URL`
