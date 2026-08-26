# Model Gateway

Provider-neutral execution boundary for local vLLM, OpenAI, and future
OpenAI-compatible model servers. Domain services submit named routes and strict
JSON schemas. The gateway owns provider selection, failover, concurrency,
idempotency, cost budgets, and metadata-only audit records; it does not decide
whether a news item is tradable and never places orders.

Default bind: `127.0.0.1:8802`.

Routes:

- `news.issuer_review.v1`
- `news.semantic_fast.v1`
- `news.trade_hypothesis.v1`
- `news.trade_hypothesis.v2`
- `sec.semantic_label.v1`
- `sec.issuer_review.v1` (manual remote review; defaults to `openai-deep`)

Runtime state is stored outside the repository under
`D:\TradingML\runtimes\model_gateway` by default.

Route timeouts are total provider budgets, not per-attempt budgets. The issuer
review route defaults to 300 seconds because structured deep-model reasoning can
legitimately exceed two minutes. Provider attempts, including failures, are
recorded in the metadata-only `inference_attempt_audit` table; `/health` exposes
bounded attempt counts and the latest result for diagnosis without storing the
source prompt.

Run with `.\scripts\run_model_gateway.ps1`.
