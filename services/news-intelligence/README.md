# News Intelligence Service

Domain service for live news eligibility and semantic labels.

The Python News Gateway owns provider acquisition and canonical V2 persistence.
After that durable publish, this service accepts a bounded candidate
notification, requires an explicitly active live-trading session, applies the
deterministic kind/scope contract and a point-in-time QMD price gate, and calls
the provider-neutral Model Gateway. It validates the current
`gpt_oss_news_semantics_v1` schema and writes the derived label to
`q_live.news_semantic_label_v1`.

Transient notification failures are healed from canonical V2 rows during the
active session. The deeper Market AI request is asynchronous and cannot block
the fast semantic label.

## Responsibilities

- Route only eligible live news; never poll a news provider.
- Freeze the point-in-time QMD snapshot used for price eligibility.
- Persist validated versioned semantic labels with model/cost/latency lineage.
- Reconcile missed live notifications from canonical V2 tables.
- Serve fast financial sentiment and relevance models from local artifacts.
- Optionally run entity/event extraction models from local artifacts.
- Optionally call an OpenAI-compatible local LLM endpoint, usually vLLM, for
  deeper classification on selected articles.
- Return stable JSON with model, prompt, and taxonomy versions.
- Keep deterministic classification as routing evidence, not as a silent
  replacement for the agreed semantic model stage.

## Environment Variables

- `NEWS_INTELLIGENCE_BIND`, default `127.0.0.1:8804`
- `NEWS_INTELLIGENCE_MODEL_ROOT`, default `D:\models_artifacts\opensource`
- `NEWS_INTELLIGENCE_MODEL_MANIFEST`, default `models\opensource_models.json`
- `NEWS_INTELLIGENCE_MODEL_DEVICE`, default `auto`; use `cuda` or `cpu` to force model device.
- `NEWS_INTELLIGENCE_STACK_VERSION`, default `news-intelligence-v1`
- `NEWS_INTELLIGENCE_TAXONOMY_VERSION`, default `news-taxonomy-v1`
- `NEWS_INTELLIGENCE_PROMPT_VERSION`, default `news-llm-prompt-v1`
- `NEWS_INTELLIGENCE_ENABLE_MODELS`, default `true`
- `NEWS_INTELLIGENCE_ENABLE_LLM`, default `false`
- `NEWS_INTELLIGENCE_LLM_BASE_URL`, default `http://127.0.0.1:8000/v1`
- `NEWS_INTELLIGENCE_LLM_MODEL`, default `Qwen/Qwen3-1.7B`
- `NEWS_INTELLIGENCE_LLM_MAX_TOKENS`, default `512`
- `NEWS_INTELLIGENCE_LLM_MERGE_MODE`, default `summary_only`; use `override` only when you want the LLM to replace structured scanner labels.
- `NEWS_INTELLIGENCE_LLM_REASONING_EFFORT`, default `low` for `gpt-oss`, otherwise empty.
- `NEWS_INTELLIGENCE_LLM_RESPONSE_FORMAT`, default `json_object` for `gpt-oss`, otherwise empty.
- `NEWS_INTELLIGENCE_ACTIVE_SENTIMENT_MODEL`, default `distilroberta-financial-news`
- `NEWS_INTELLIGENCE_ACTIVE_NER_MODEL`, default `quantbridge-energy-intelligence`
- `NEWS_INTELLIGENCE_MAX_TEXT_CHARS`, default `6000`
- `NEWS_INTELLIGENCE_LLM_MIN_MATERIALITY`, default `0.65`
- `NEWS_INTELLIGENCE_LLM_MIN_TEXT_CHARS`, default `80`
- `NEWS_INTELLIGENCE_LLM_TIMEOUT_MS`, default `3500`
- `NEWS_INTELLIGENCE_MODEL_GATEWAY_URL`, default `http://127.0.0.1:8802`
- `NEWS_INTELLIGENCE_MARKET_AI_URL`, default `http://127.0.0.1:8803`
- `NEWS_INTELLIGENCE_BACKEND_URL`, default `http://127.0.0.1:8000`; this is
  polled to restore the explicit Live-session gate after either service restarts
- `NEWS_INTELLIGENCE_QMD_URL`, default `http://127.0.0.1:8795`
- `NEWS_INTELLIGENCE_ALLOWED_KINDS`, default `company,regulatory,analyst,editorial`
- `NEWS_INTELLIGENCE_MAX_PRICE`, default `50`
- `NEWS_INTELLIGENCE_WORKERS`, default `4`
- `NEWS_INTELLIGENCE_QUEUE_MAX`, default `4096`
- `NEWS_INTELLIGENCE_RECONCILE_SECONDS`, default `10`

## Run

Install dependencies in the Python environment that will host the models:

```powershell
pip install -r services\news-intelligence\requirements.txt
```

```powershell
cd services\news-intelligence
python -m news_intelligence.main
```

or:

```powershell
.\scripts\run_news_intelligence.ps1
```

## Download Models

```powershell
python services\news-intelligence\scripts\download_models.py
```

The default target is `D:\models_artifacts\opensource`. Large and gated models
are listed in the manifest but are skipped unless explicitly enabled.

```powershell
python services\news-intelligence\scripts\download_models.py --include-large
python services\news-intelligence\scripts\download_models.py --include-gated
```

To download only OpenAI `gpt-oss-20b` for offline/vLLM testing:

```powershell
python services\news-intelligence\scripts\download_models.py --only openai-gpt-oss-20b --include-large
```

## Serving Local LLMs

The intelligence service expects an OpenAI-compatible local LLM endpoint when
`NEWS_INTELLIGENCE_ENABLE_LLM=true`. For `gpt-oss-20b`, the recommended path is
vLLM on compatible GPU hardware:

```powershell
.\scripts\run_news_llm_vllm.ps1 -ModelKey openai-gpt-oss-20b
```

Then point the intelligence service at it:

```powershell
$env:NEWS_INTELLIGENCE_ENABLE_LLM = "true"
$env:NEWS_INTELLIGENCE_LLM_BASE_URL = "http://127.0.0.1:8000/v1"
$env:NEWS_INTELLIGENCE_LLM_MODEL = "openai/gpt-oss-20b"
```

`gpt-oss-20b` is intended for self-managed serving. It is not available through
the OpenAI API or ChatGPT.

## API

```text
GET /health
GET /models
POST /classify
```

The `/classify` response is intentionally provider-neutral. The gateway maps it
onto persisted ClickHouse columns and websocket summary fields.
