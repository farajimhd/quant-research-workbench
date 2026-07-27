# GPT-OSS semantic news labeling v1

This version develops and audits one semantic taxonomy with local
`openai/gpt-oss-20b` and `openai/gpt-oss-120b`. It freezes one stratified
sample and evaluates both models against identical text, prompts, schemas, and
decoding settings. It does not write model judgments into a production
ClickHouse authority.

## Design boundary

The workflow keeps three concerns separate:

1. Deterministic identity, timestamps, ticker scope, provider metadata, hashes,
   renderer provenance, and existing rule labels remain authoritative code and
   data.
2. The local language model judges article meaning: source role, relationship
   to the issuer, semantic events, component sentiment, modality, novelty, and
   impact horizon.
3. Future market reaction is excluded. Reaction labels remain a separate
   event-derived product and must never leak into language sentiment.

Ticker count is never proof of company news. `company_announcement=true`
requires either a direct issuer announcement or a report of a concrete
issuer-originated event.

## Output contract

Every article produces:

- `source`: origin, content role, issuer relationship, company-announcement
  judgment, and confidence.
- `events`: up to eight independently directed semantic events with family,
  subtype, intensity, time orientation, modality, and confidence.
- `sentiment`: an overall text-only score from -100 to +100 plus independent
  dimensions for historical performance, outlook, demand, operations,
  liquidity, dilution, regulation/legal, management, and any reported market
  reaction.
- `novelty`: new event, material update, repeat, recap, preview, or unknown,
  plus the article-implied impact horizon.
- `quality`: explicit uncertainty and rendering/content flags.
- `evidence`: at most six short verbatim excerpts.

The event catalog covers earnings; guidance; capital return; financing; capital
structure; M&A; contracts/orders; products; clinical; regulatory; legal;
management/governance; operations; credit/solvency; analyst actions; ownership;
accounting/audit; listing/market structure; cybersecurity/privacy; intellectual
property; macro/sector; reported market activity; ordinary media/corporate
activity; and an explicit `other` escape hatch. Exact codes and subtypes are in
`taxonomy.py`.

This contract is multi-label and component-based. One story can contain an
earnings beat and a guidance cut without collapsing either fact.

## Guardrails

- The certified structured-rendering v2 authority must be ready before database
  sampling.
- Both models consume the exact same frozen `sample.jsonl`, including identical
  rendered-text hashes.
- Existing completed labels are resumable only if the rendered-text SHA-256,
  label version, prompt version, and model identity are unchanged.
- The model must use the strict JSON schema.
- Unknown enum values, impossible company-announcement combinations,
  non-finite confidence, duplicate dimensions, and non-verbatim evidence fail
  validation.
- Each completed item is durably appended before the next progress update.
- Failures are retained separately and never counted as valid labels.
- No raw secrets, source text, SQL payloads, or hidden reasoning enter logs.
- All generated samples, results, audits, and comparisons stay under
  `D:\TradingML\runtimes`, never the source repository.

## Controlled 20B-versus-120B experiment

Do not serve both models concurrently for this benchmark. Concurrent GPU
contention makes latency and throughput incomparable. Use the same GPU
reservation, 16,384-token context, four client workers, frozen sample, prompt,
schema, and decoding settings for both runs.

After the structured-rendering v2 rebuild reports `status=ready`, freeze the
comparison population once:

```powershell
conda activate ml4t
cd D:\TradingML\codes\quant_research_workbench_pipelines
python -m research.news_labeling.gpt_oss_v1.run_prepare_comparison
```

Start 20B in the workstation environment where vLLM and the model are already
installed:

```bash
VLLM_USE_FLASHINFER_SAMPLER=0 vllm serve openai/gpt-oss-20b \
  --host 0.0.0.0 --port 8000 \
  --served-model-name openai/gpt-oss-20b \
  --gpu-memory-utilization 0.88 --max-model-len 16384 \
  --safetensors-load-strategy prefetch --enable-prefix-caching
```

Wait for application startup, verify `http://127.0.0.1:8000/v1/models`, then
run 20B in PowerShell:

```powershell
conda activate ml4t
cd D:\TradingML\codes\quant_research_workbench_pipelines
python -m research.news_labeling.gpt_oss_v1.run_sample --profile 20b `
  --input-jsonl D:\TradingML\runtimes\news_labeling\gpt_oss_v1\shared\sample.jsonl `
  --execute
```

Stop only the 20B server. Start 120B with the same settings:

```bash
VLLM_USE_FLASHINFER_SAMPLER=0 vllm serve openai/gpt-oss-120b \
  --host 0.0.0.0 --port 8000 \
  --served-model-name openai/gpt-oss-120b \
  --gpu-memory-utilization 0.88 --max-model-len 16384 \
  --safetensors-load-strategy prefetch --enable-prefix-caching
```

If the installed models are filesystem paths rather than Hugging Face cache
identifiers, replace the model argument with that path while retaining
`--served-model-name`. If 120B is sharded across GPUs, add the installation's
required `--tensor-parallel-size`; do not change it between repeated 120B speed
runs.

Run 120B against the exact same saved sample:

```powershell
python -m research.news_labeling.gpt_oss_v1.run_sample --profile 120b `
  --input-jsonl D:\TradingML\runtimes\news_labeling\gpt_oss_v1\shared\sample.jsonl `
  --execute
```

Then generate the comparison:

```powershell
python -m research.news_labeling.gpt_oss_v1.run_compare_models
```

The report measures wall throughput, per-request mean/median/P95 latency,
completion-token rate, failures, field agreement, exact event-set agreement,
and event Jaccard similarity. It also creates reviewable Markdown dossiers for
the 48 strongest disagreements.

Cross-model agreement is not semantic accuracy. To calculate accuracy, create a
reviewed JSONL answer key containing `canonical_news_id` and a complete `label`
object, then run:

```powershell
python -m research.news_labeling.gpt_oss_v1.run_compare_models `
  --answer-key-jsonl D:\TradingML\runtimes\news_labeling\gpt_oss_v1\answer_key.jsonl
```

That adds per-field accuracy and event precision, recall, and F1 for both
models. Using 120B's own output as the answer key is prohibited because it
would make the comparison circular.

Defaults are 192 articles, 4,000 stable-hash candidates, and four identical
bounded concurrent requests per model. Generated outputs are:

```text
D:\TradingML\runtimes\news_labeling\gpt_oss_v1
|-- shared\sample.jsonl
|-- models\20b\
|-- models\120b\
`-- comparison\
```

The sampler uses the exact locally cached tokenizer for context budgeting. Pass
an installed filesystem path with `--tokenizer-source` when the tokenizer is
not in the standard Hugging Face cache. It measures the complete
Harmony-formatted request and truncates only the article tail when necessary,
reserving 1,536 tokens for the structured response.

Do not scale either model to the full corpus from these commands. First inspect
both audits, adjudicate the disagreement set into a blind answer key, and select
the model from semantic accuracy, contract reliability, and measured operating
cost together.
