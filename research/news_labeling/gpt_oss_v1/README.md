# gpt-oss semantic news labeling v1

This version is the taxonomy-and-prompt development stage for local
`openai/gpt-oss-20b`. It creates a small, stratified, human-auditable research
sample. It does **not** write model judgments into a production ClickHouse
authority.

## Design boundary

The workflow keeps three concerns separate:

1. Deterministic identity, timestamps, ticker scope, provider metadata, hashes,
   renderer provenance, and existing rule labels remain authoritative code/data.
2. The local language model judges article meaning: source role, relationship
   to the issuer, semantic events, component sentiment, modality, novelty, and
   impact horizon.
3. Future market reaction is excluded. Reaction labels remain a separate
   event-derived product and must never leak into language sentiment.

Ticker count is never used as proof of company news. `company_announcement=true`
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
  liquidity, dilution, regulation/legal, management, and any *reported* market
  reaction.
- `novelty`: new event, material update, repeat, recap, preview, or unknown;
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

This first version is intentionally multi-label and component-based. A story can
contain an earnings beat and a guidance cut without collapsing either fact.

## Guardrails

- The certified structured-rendering v2 authority must be ready before database
  sampling.
- Existing completed labels are resumable only if the rendered-text SHA-256 is
  unchanged.
- The model must use the strict JSON schema.
- Unknown enum values, impossible company-announcement combinations,
  non-finite confidence, duplicate dimensions, and non-verbatim evidence fail
  validation.
- Each completed item is appended and flushed before the next progress update.
- Failures are retained separately and never counted as valid labels.
- No raw secrets or hidden reasoning are logged.

## Workstation commands

The workstation already has vLLM inside WSL; do not install a second Windows
copy. From a Windows terminal, start that existing runtime with:

```powershell
conda activate ml4t
cd D:\TradingML\codes\quant-research-workbench
python -m research.news_labeling.gpt_oss_v1.run_server_wsl
```

This executes `vllm serve openai/gpt-oss-20b` inside the default WSL
distribution, binds it to port 8000, and enables prefix caching. If vLLM is
installed in a non-default distribution, add `--distro <name>`. Windows
localhost forwarding must be enabled so the labeling client can reach
`http://127.0.0.1:8000/v1`.

Alternatively, start it directly in the existing WSL shell:

```bash
vllm serve openai/gpt-oss-20b --host 0.0.0.0 --port 8000 \
  --gpu-memory-utilization 0.85 --max-model-len 16384 \
  --enable-prefix-caching
```

In a second terminal, first create and inspect the deterministic sample plan:

```powershell
conda activate ml4t
cd D:\TradingML\codes\quant-research-workbench
python -m research.news_labeling.gpt_oss_v1.run_sample
```

Then label it:

```powershell
python -m research.news_labeling.gpt_oss_v1.run_sample --execute
```

Defaults are 192 articles, 4,000 stable-hash candidates, and eight bounded
concurrent requests. Outputs are under:

```text
D:\market-data\prepared\news_labeling\gpt_oss_v1
├── sample.jsonl
├── labels.jsonl
├── failures.jsonl
├── manifest.json
├── AUDIT.md
└── samples\
```

Do not scale to the full corpus from this command. First inspect the audit,
revise ambiguous definitions and prompt failures, create a blind answer set,
and compare 20b with a stronger judge on exactly the same frozen sample.
