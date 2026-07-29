# Text Intelligence Service

Shared domain service for deterministic News and SEC text labels, with an
optional live News model path.

News Gateway and SEC Gateway own acquisition and canonical persistence. After a
canonical publish, they send only a corpus, source identity, and timestamp to
this service. Bounded workers reload the canonical rendered authority, apply
the shared `scoped_text_labeling_v4` issuer/event classifier, and persist
versioned rows to `q_live.scoped_text_labels_v4` and
`q_live.scoped_content_relations_v2`.

`q_live.scoped_text_live_status_v2` binds completion to the exact rendered
source hash. Reconciliation therefore repairs missed notices and reprocesses
new News or SEC revisions without repeating current work. Deterministic labels
run regardless of trading state. Only the optional live News model path
requires an active Live session and point-in-time QMD price eligibility.

## Responsibilities

- Classify every completed canonical News and SEC document; never poll a source
  provider.
- Maintain source-hash idempotency and repair missed gateway notifications.
- Preserve one canonical publication while separating issuer roles, evidence,
  concepts, eligibility, and direction for multi-issuer events.
- Persist News and SEC relationships through the same versioned authority.
- Route only eligible News issuer units into optional live model inference.
- Freeze the point-in-time QMD snapshot used for price eligibility.
- Persist validated versioned semantic labels with model/cost/latency lineage.
- Reconcile missed or revised canonical News and SEC text.
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
- `NEWS_INTELLIGENCE_MAX_PRICE`, default `50`
- `NEWS_INTELLIGENCE_WORKERS`, default `4`
- `NEWS_INTELLIGENCE_QUEUE_MAX`, default `4096`
- `NEWS_INTELLIGENCE_RECONCILE_SECONDS`, default `10`
- `NEWS_INTELLIGENCE_LABEL_TABLE`, default `news_semantic_label_v2`
- `TEXT_INTELLIGENCE_WORKERS`, default `4`, bounded to `16`
- `TEXT_INTELLIGENCE_QUEUE_MAX`, default `8192`
- `TEXT_INTELLIGENCE_RECONCILE_SECONDS`, default `30`
- `TEXT_INTELLIGENCE_RECONCILE_HOURS`, default `72`

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
POST /documents
```

`POST /documents` accepts a bounded batch of lightweight canonical notices:

```json
{"documents":[{"corpus":"news","source_id":"canonical-id","source_timestamp":"2026-07-28T14:30:00Z"}]}
```

`POST /classify` remains the provider-neutral interactive model API. It is not
the persistence authority used by the gateways.
