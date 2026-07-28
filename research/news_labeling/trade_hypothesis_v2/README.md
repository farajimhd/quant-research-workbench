# Trade Hypothesis V2

This experiment compares Sol and local GPT-OSS-120B on the same frozen
population of exactly 90 single-ticker articles selected from the established
192-article news-label sample.

## Contract

Each model receives the same immutable, point-in-time context:

- the V2 rendered title and article text;
- the existing semantic news label;
- the ticker's QMD-equivalent market snapshot reconstructed strictly through
  the publication timestamp;
- point-in-time market-hours, SEC, and fundamental context;
- the latest three earlier same-ticker news articles;
- for each earlier article, only reaction horizons whose observation window
  had completed by the current article's publication timestamp.

The model predicts upside, downside, and no-action probabilities, expected
return, favorable excursion, adverse excursion, confidence, and abstention for
these certified reaction horizons:

- `1m`
- `5m`
- `30m`
- `regular_close`
- `extended_close`

Targets are stored beside the model output for later scoring but are never
included in the model prompt. The context build rejects population drift unless
the frozen source still contains 192 articles and exactly 90 are single-ticker.

Both runners share
`D:\TradingML\runtimes\news_labeling\trade_hypothesis_v2\shared\contexts.jsonl`.
An atomic preparation lock allows the runners to start in parallel without
building different contexts or duplicating the database work. Generated
contexts, requests, responses, state, and logs remain outside the repository.

## Run in parallel

Start the local vLLM server with `openai/gpt-oss-120b`, then use two PowerShell
terminals in the `ml4t` environment.

Terminal 1:

```powershell
python -m research.news_labeling.trade_hypothesis_v2.run_oss120 --execute
```

Terminal 2:

```powershell
python -m research.news_labeling.trade_hypothesis_v2.run_sol_batch --execute --authorize-cost-usd 10
```

The Sol command is resumable. Add `--no-wait` to submit and return immediately;
rerun the same command without `--no-wait` to reconcile, download, validate,
and materialize its results. Its hard Batch cost ceiling is USD 10. The command
prints a conservative protected bound and refuses submission unless the
explicit `--authorize-cost-usd` value covers it; authorization is a ceiling,
not a spending target.

The OSS runner is also resumable: every completed article is durably written,
and a rerun processes only missing or failed identities.
