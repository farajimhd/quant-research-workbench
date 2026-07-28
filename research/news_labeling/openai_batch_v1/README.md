# OpenAI remote news-label comparison

This module applies the existing `gpt_oss_v1` taxonomy, frozen input sample,
prompt, structured-output schema, and deterministic validator to exactly these
remote model aliases:

- `gpt-5.6-sol`
- `gpt-5.6-terra`
- `gpt-5.6-luna`
- `gpt-5.4-mini`
- `gpt-5.4-nano`
- `gpt-4.1-mini`
- `gpt-4.1-nano`

It uses the OpenAI Batch API. The default command is planning-only and makes no
remote request:

```powershell
python -m research.news_labeling.openai_batch_v1.run_experiment
```

The plan is calculated from the actual serialized request bodies and reserves
the complete configured output allowance. Review the printed protected total,
then submit and exit:

```powershell
python -m research.news_labeling.openai_batch_v1.run_experiment --execute --authorize-cost-usd 20 --no-wait
```

Later, rerun without `--no-wait` to reconcile the existing remote batches,
persist and validate labels, produce per-model audits, and write the pairwise
comparison:

```powershell
python -m research.news_labeling.openai_batch_v1.run_experiment --execute --authorize-cost-usd 20
```

The authorization is a ceiling, not a spending target. Submission is rejected
unless the authenticated project exposes every requested alias, the protected
plan fits below the hard `$20.00` ceiling, and the explicit authorization covers
that plan. State and Batch IDs are persisted before polling so reruns reconcile
rather than resubmit. Changing the sample, prompt, schema, token allowance, or
model matrix requires a new runtime root.

The API key is loaded from `OPENAI_API_KEY`; it is never written to plans,
manifests, logs, requests, or comparison files. The default runtime output is:

```text
D:\TradingML\runtimes\news_labeling\openai_batch_v1
```

Important outputs:

- `plan.json`: frozen cost and request contract
- `models/<alias>/state.json`: resumable remote Batch identity and status
- `models/<alias>/labels.jsonl`: validated durable labels
- `models/<alias>/failures.jsonl`: explicit rejected or missing results
- `models/<alias>/AUDIT.md`: per-model readable audit
- `comparison/COMPARISON.md`: completion, cost, and pairwise agreement
- `comparison/disagreements/*.md`: highest-disagreement articles with all labels

Agreement is not accuracy. The comparison measures cross-model consistency; a
reviewed answer key is still required to establish semantic accuracy. Supply one
with `--answer-key-jsonl <path>` to add field accuracy and event
precision/recall/F1 to the same report. Batch elapsed time is reported as
operational throughput evidence, but queue time means it is not a controlled
latency benchmark.
