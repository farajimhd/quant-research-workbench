# Model Gateway

Provider-neutral execution boundary for local vLLM, OpenAI, and future
OpenAI-compatible model servers. Domain services submit named routes and strict
JSON schemas. The gateway owns provider selection, failover, concurrency,
idempotency, cost budgets, and metadata-only audit records; it does not decide
whether a news item is tradable and never places orders.

Default bind: `127.0.0.1:8802`.

Routes:

- `news.semantic_fast.v1`
- `news.trade_hypothesis.v1`
- `news.trade_hypothesis.v2`
- `sec.semantic_label.v1`

Runtime state is stored outside the repository under
`D:\TradingML\runtimes\model_gateway` by default.

Run with `.\scripts\run_model_gateway.ps1`.
