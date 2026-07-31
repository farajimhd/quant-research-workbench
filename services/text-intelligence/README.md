# Text Intelligence Service

Shared domain service for deterministic News and SEC text labels, with an
optional live News model path.

News Gateway and SEC Gateway own acquisition and canonical persistence. After a
canonical publish, they send only a corpus, source identity, timestamp, and
the SEC CIK needed for an exact filing read to this service. Bounded workers
reload the canonical rendered authority, apply
the shared `scoped_text_labeling_v5` issuer/event classifier, and persist
compact versioned rows to `q_live.scoped_text_labels_v5` and
`q_live.scoped_content_relations_v3`. Canonical rendered text is referenced by
hash and bounded offsets; the live service does not duplicate full text,
blocks, or per-span context into production label rows.

`q_live.scoped_text_live_status_v2` binds completion to the exact rendered
source hash. Reconciliation therefore repairs missed notices and reprocesses
new News or SEC revisions without repeating current work. Deterministic labels
run regardless of trading state. Only the optional live News model path
requires an active Live session and point-in-time QMD price eligibility.
SEC filings whose canonical document taxonomy is intentionally non-narrative
are durably completed with zero labels and a revision-sensitive ineligibility
hash. Eligible filings whose rendered text is not ready are deferred for the
next reconciliation cycle rather than misreported as processing failures.

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

- `TEXT_INTELLIGENCE_BIND`, default `127.0.0.1:8804`
- `TEXT_INTELLIGENCE_MODEL_ROOT`, default `D:\models_artifacts\opensource`
- `TEXT_INTELLIGENCE_MODEL_MANIFEST`, default `models\opensource_models.json`
- `TEXT_INTELLIGENCE_MODEL_DEVICE`, default `auto`; use `cuda` or `cpu` to force model device.
- `TEXT_INTELLIGENCE_STACK_VERSION`, default `text-intelligence-v1`
- `TEXT_INTELLIGENCE_TAXONOMY_VERSION`, default `news-taxonomy-v1`
- `TEXT_INTELLIGENCE_PROMPT_VERSION`, default `news-llm-prompt-v1`
- `TEXT_INTELLIGENCE_ENABLE_MODELS`, default `false`; set `true` explicitly to
  load the configured optional local sentiment/NER models.
- `TEXT_INTELLIGENCE_ENABLE_LLM`, default `false`
- `TEXT_INTELLIGENCE_ENABLE_LIVE_AI`, default `false`; when `true`, eligible
  News during an explicitly active Live session is routed through Model
  Gateway and then optionally to Market AI. This setting does not affect the
  required deterministic News/SEC classifier.
- `TEXT_INTELLIGENCE_LLM_BASE_URL`, default `http://127.0.0.1:8000/v1`
- `TEXT_INTELLIGENCE_LLM_MODEL`, default `Qwen/Qwen3-1.7B`
- `TEXT_INTELLIGENCE_LLM_MAX_TOKENS`, default `512`
- `TEXT_INTELLIGENCE_LLM_MERGE_MODE`, default `summary_only`; use `override` only when you want the LLM to replace structured scanner labels.
- `TEXT_INTELLIGENCE_LLM_REASONING_EFFORT`, default `low` for `gpt-oss`, otherwise empty.
- `TEXT_INTELLIGENCE_LLM_RESPONSE_FORMAT`, default `json_object` for `gpt-oss`, otherwise empty.
- `TEXT_INTELLIGENCE_ACTIVE_SENTIMENT_MODEL`, default `distilroberta-financial-news`
- `TEXT_INTELLIGENCE_ACTIVE_NER_MODEL`, default `quantbridge-energy-intelligence`
- `TEXT_INTELLIGENCE_MAX_TEXT_CHARS`, default `6000`
- `TEXT_INTELLIGENCE_LLM_MIN_MATERIALITY`, default `0.65`
- `TEXT_INTELLIGENCE_LLM_MIN_TEXT_CHARS`, default `80`
- `TEXT_INTELLIGENCE_LLM_TIMEOUT_MS`, default `3500`
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
- `TEXT_INTELLIGENCE_TERMINAL_RICH_ENABLED`, default `auto` from interactive
  stdout. The live-gateway starter sets it to `true` for the Text Intelligence
  tab.
- `TEXT_INTELLIGENCE_TERMINAL_SCREEN_ENABLED`, default `true`
- `TEXT_INTELLIGENCE_TERMINAL_REFRESH_SECONDS`, default `1.0`

Common service settings formerly named `NEWS_INTELLIGENCE_*` remain accepted
as transitional aliases. `TEXT_INTELLIGENCE_*` always takes precedence. The
remaining `NEWS_INTELLIGENCE_*` settings above configure the optional News-only
live inference route inside this shared service; they do not name the service.

## Run

Install dependencies in the Python environment that will host the models:

```powershell
pip install -r services\text-intelligence\requirements.txt
```

```powershell
cd services\text-intelligence
python -m text_intelligence.main
```

or:

```powershell
.\scripts\run_text_intelligence.ps1
```

The bare launcher runs the required deterministic News/SEC V5 classifier and
reconciler only. It does not load local language models and does not call an
LLM, Model Gateway, or Market AI. The standard live-gateway launcher also
starts this deterministic service as its fifth tab with the shared Rich
operational terminal. That terminal keeps current reconciliation, worker
focus, queue depth, durable completion counts, and active failures visible;
redirected/non-interactive starts remain cursor-control free. Optional
inference is an explicit operator choice:

```powershell
# Optional local sentiment/NER models.
$env:TEXT_INTELLIGENCE_ENABLE_MODELS = "true"

# Optional OpenAI-compatible LLM route.
$env:TEXT_INTELLIGENCE_ENABLE_LLM = "true"

# Optional live-trading AI route. Model Gateway must be running; Market AI is
# the separate deeper-hypothesis consumer.
$env:TEXT_INTELLIGENCE_ENABLE_LIVE_AI = "true"
.\scripts\run_text_intelligence.ps1
```

Unset the optional variables, or set them to `false`, to return to deterministic-only
operation.

### Starting after a historical backfill

The finite V5 backfill and this continuous service may run concurrently because
their label and relationship identities are idempotent. Delaying the service is
safe only when no canonical News/SEC rows arrive outside the finite backfill's
fixed date range, or when every missed row remains inside the service's recent
reconciliation window when it eventually starts.

The historical runner fixes `--end-date-exclusive` when it is launched. Rows
published after that boundary are not part of that run. On service startup,
`TEXT_INTELLIGENCE_RECONCILE_HOURS` defaults to 72 hours and is bounded to 720
hours. Therefore, if gateways keep ingesting while a long backfill runs, start
this service concurrently. Otherwise, post-boundary rows older than the
reconciliation window require an explicit range-scoped V5 rebuild.

## Download Models

```powershell
python services\text-intelligence\scripts\download_models.py
```

The default target is `D:\models_artifacts\opensource`. Large and gated models
are listed in the manifest but are skipped unless explicitly enabled.

```powershell
python services\text-intelligence\scripts\download_models.py --include-large
python services\text-intelligence\scripts\download_models.py --include-gated
```

To download only OpenAI `gpt-oss-20b` for offline/vLLM testing:

```powershell
python services\text-intelligence\scripts\download_models.py --only openai-gpt-oss-20b --include-large
```

## Serving Local LLMs

The intelligence service expects an OpenAI-compatible local LLM endpoint when
`TEXT_INTELLIGENCE_ENABLE_LLM=true`. For `gpt-oss-20b`, the recommended path is
vLLM on compatible GPU hardware:

```powershell
.\scripts\run_news_llm_vllm.ps1 -ModelKey openai-gpt-oss-20b
```

Then point the intelligence service at it:

```powershell
$env:TEXT_INTELLIGENCE_ENABLE_LLM = "true"
$env:TEXT_INTELLIGENCE_LLM_BASE_URL = "http://127.0.0.1:8000/v1"
$env:TEXT_INTELLIGENCE_LLM_MODEL = "openai/gpt-oss-20b"
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
