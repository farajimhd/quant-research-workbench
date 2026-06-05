# News Intelligence Service

Standalone model-serving service for live news labels.

This service does not poll providers and does not write ClickHouse. The Rust
`news-gateway` owns ingestion, persistence, and websocket streaming. This
service receives one normalized article, runs the configured model tiers, and
returns labels that the gateway can persist and broadcast with the article.

## Responsibilities

- Serve fast financial sentiment and relevance models from local artifacts.
- Optionally run entity/event extraction models from local artifacts.
- Optionally call an OpenAI-compatible local LLM endpoint, usually vLLM, for
  deeper classification on selected articles.
- Return stable JSON with model, prompt, and taxonomy versions.
- Degrade to deterministic fallback labels if model packages or artifacts are
  missing, so the news gateway never blocks ingestion.

## Environment Variables

- `NEWS_INTELLIGENCE_BIND`, default `127.0.0.1:8797`
- `NEWS_INTELLIGENCE_MODEL_ROOT`, default `D:\models_artifacts\opensource`
- `NEWS_INTELLIGENCE_MODEL_MANIFEST`, default `models\opensource_models.json`
- `NEWS_INTELLIGENCE_STACK_VERSION`, default `news-intelligence-v1`
- `NEWS_INTELLIGENCE_TAXONOMY_VERSION`, default `news-taxonomy-v1`
- `NEWS_INTELLIGENCE_PROMPT_VERSION`, default `news-llm-prompt-v1`
- `NEWS_INTELLIGENCE_ENABLE_MODELS`, default `true`
- `NEWS_INTELLIGENCE_ENABLE_LLM`, default `false`
- `NEWS_INTELLIGENCE_LLM_BASE_URL`, default `http://127.0.0.1:8000/v1`
- `NEWS_INTELLIGENCE_LLM_MODEL`, default `Qwen/Qwen3-1.7B`
- `NEWS_INTELLIGENCE_ACTIVE_SENTIMENT_MODEL`, default `distilroberta-financial-news`
- `NEWS_INTELLIGENCE_ACTIVE_NER_MODEL`, default `quantbridge-energy-intelligence`
- `NEWS_INTELLIGENCE_MAX_TEXT_CHARS`, default `6000`
- `NEWS_INTELLIGENCE_LLM_MIN_MATERIALITY`, default `0.65`
- `NEWS_INTELLIGENCE_LLM_MIN_TEXT_CHARS`, default `80`
- `NEWS_INTELLIGENCE_LLM_TIMEOUT_MS`, default `3500`

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

## API

```text
GET /health
GET /models
POST /classify
```

The `/classify` response is intentionally provider-neutral. The gateway maps it
onto persisted ClickHouse columns and websocket summary fields.
