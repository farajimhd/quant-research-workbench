# News Synthesis Provider-Context Router V1

> Historical baseline. The current implementation and evaluation are documented
> in `NEWS_SYNTHESIS_FUNNEL_V4.md`.

## Outcome

`news_synthesis_provider_context_router_v1` is the cheap, deterministic front door for the News Synthesis funnel. It returns one of three routes before issuer identity resolution and semantic extraction:

- `forecast_candidate`: continue through normal issuer-forecast synthesis.
- `context_only`: bypass the expensive issuer-forecast lane, but preserve the article for the separate market-context lane.
- `semantic_rescue_required`: metadata identifies a mixed family, so semantic synthesis must decide.

The normal V49 synthesis document also carries the complete decision under `envelope.provider_context`. The standalone `classify_provider_context(source)` API lets a caller make the decision before invoking the engine and therefore realize the compute saving.

## V1 authority and precedence

Exact provider tags are treated as versioned provider-family evidence. The corrected 2025-August 2026 authority supports these `context_only` families:

| Family | Provider tags |
|---|---|
| Automated price/options digest | `bzi-pod` |
| Automated ticker-mover feed | `bzi-tfm` |
| Trading halt notice | `halts` |
| Automated unusual-options activity | `bzi-auoa` |
| Analyst rating roundup | `top upgrades`, `top downgrades` |
| Analyst forecast roundup | `analysts forecasts` |
| Scheduled earnings preview | `bzi-ep` |

Mixed tags such as `bzi-recaps`, `big losers`, `big gainers`, `mid morning market update`, `mid day market update`, and `mid day movers` always require semantic rescue. If a row contains both a context-only and mixed tag, rescue wins. A generic material-keyword regex cannot override an exact validated context family; the previous contradiction audit demonstrated that these keyword appearances are not reliable semantic authority inside provider templates.

Temporal novelty is causal and point-in-time: the evaluation computes whether a ticker is first in its New York session, its session ordinal, and time since prior ticker news using strictly earlier publication timestamps. V1 records these values but does not reject on them because their standalone eligibility rates are mixed and drift across splits.

## Corrected 2025-2026 evaluation

Generated artifacts are outside the repository at:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\provider_context_router_evaluation_v1`

The evaluation joined the corrected provider-filter successor labels to the original metadata and hash-verified rendered-text authority. Insufficient labels were excluded.

| Metric | Result |
|---|---:|
| Decisive articles | 346,107 |
| Corrected eligible | 126,027 |
| Corrected ineligible | 220,080 |
| Context-only | 21,823 |
| Estimated expensive forecast-lane reduction | 6.31% |
| Context-only eligible conflicts | 0 |
| Retained corrected eligible recall | 100.0000% |
| Context-only noise precision | 100.0000% |
| 95% Wilson lower bound | 99.9824% |
| Semantic rescue | 4,955 (1.43%) |

Split results remained at zero eligible conflicts:

| Split | Articles | Context-only | Estimated reduction |
|---|---:|---:|---:|
| 2025 discovery | 203,849 | 14,375 | 7.05% |
| Jan-Apr 2026 validation | 71,647 | 4,950 | 6.91% |
| May-Aug 2026 final | 70,611 | 2,498 | 3.54% |

## Activation boundary

The router and trace are implemented, but the live/backfill callers must not silently discard `context_only` rows. Actual skip activation requires a durable context-lane record plus routed, retained, failed, and replayable counts. Until that storage contract is added, callers can run this router in shadow mode or explicitly preserve context-only sources before skipping `NewsSynthesisEngine.synthesize()`.

This evaluation is development evidence on the corrected in-period authority. Production certification still requires a fresh post-August holdout and a live replay ordered by true `available_at`, not only publication time.
