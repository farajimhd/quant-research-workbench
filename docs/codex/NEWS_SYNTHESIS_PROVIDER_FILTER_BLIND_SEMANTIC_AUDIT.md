# News Synthesis Provider-Filter Blind Semantic Audit

## Outcome

An isolated reviewer labeled all 709 candidate feature rows without access to candidate supports, eligible/ineligible counts, rates, temporal grades, the prior analysis report, or rule-waterfall results.

- `likely_eligible`: 536
- `likely_ineligible`: 173
- Forward-supported candidates: 100
- Forward-supported high disagreements: 57
- Provisional disagreements without the forward-support floor: 36
- Opposite-direction semantic rejection risks: 5

The 57 high-disagreement rows are highly overlapping representations of four semantic families, not 57 independent rules:

- 50 analyst-action predicates: analyst ratings, price targets, initiations, reiterations, upgrades, and downgrades
- 3 halt predicates
- 2 bare `trading ideas` predicates
- 2 `short ideas` predicates

## Blind protocol

The controller randomized candidate order using a hash-derived blind ID and built a packet containing only:

- the exact feature predicate and category;
- up to one unlabeled example from each temporal split;
- provider tags, channels, publication/session metadata, ticker metadata, content-quality flags, causal ticker context, semantic text flags, and full rendered source text.

The packet explicitly excluded current labels, supports, rates, confidence intervals, candidate grades, odds ratios, mutual information, waterfall results, and reports. It contained 709 unique predicates and 1,849 unlabeled examples. Its SHA-256 is `ec419cd44dc8aa8c50c2129ebe2d575734e97b369111bc29c9cd19f1c2bfdf99`.

The blind reviewer used a conservative fail-open policy: label a predicate `likely_ineligible` only when the predicate itself reliably denotes a repetitive, derivative, scheduled, or non-new-information template. A broad predicate that can mix material issuer news with noise was labeled `likely_eligible`.

After all 709 labels were frozen and membership-validated, the controller joined the hidden empirical results.

## High disagreements

A high disagreement requires all of the following:

- the blind semantic label is `likely_eligible`;
- the observed eligible rate is at most 5% in every populated discovery, validation, and final split;
- each temporal split contains at least 30 matching rows.

### Direct analyst actions versus analyst roundups

This is the dominant disagreement. The blind reviewer considered individual analyst actions potentially new and price-relevant, while the existing authority labels nearly all of them ineligible.

Representative results:

| Predicate | Total support | Overall eligible | Final support | Final eligible |
|---|---:|---:|---:|---:|
| `channel_set=analyst ratings|news|price target` | 61,888 | 0.00% | 14,299 | 0.00% |
| `channel=analyst ratings` | 112,327 | 1.65% | 22,679 | 2.49% |
| `channel=price target` | 96,891 | 1.23% | 21,480 | 1.66% |
| `channel=initiation` | 7,691 | 0.29% | 1,629 | 0.37% |
| `channel=downgrades` | 5,054 | 1.03% | 1,015 | 0.49% |
| `channel=upgrades` | 4,671 | 0.73% | 795 | 0.13% |
| `rule:price_target_without_material_override` | 97,057 | 0.82% | 21,138 | 1.12% |

The same blind pass labeled explicit roundup predicates such as `top upgrades`, `top downgrades`, `analysts forecasts`, and `most accurate analysts` as `likely_ineligible`. This is the useful deterministic distinction: direct analyst actions and aggregate/recycled analyst lists should not share one policy merely because both carry analyst metadata.

The disagreement is primarily a forecast-eligibility policy question, not a statistical failure. If a new rating, initiation, upgrade, downgrade, or price-target change is considered forecast-relevant information, the current label authority is too restrictive. If the funnel intentionally excludes analyst opinion regardless of price relevance, the labels are internally consistent but the policy must be explicit.

### Halt predicates

`tag=halts`, `tag_set=halts`, and the one-ticker halt metadata signature have 2,148-2,165 observations, zero current eligible labels, and 148-153 final-period observations with zero eligible labels. The blind reviewer still labeled them `likely_eligible` because a halt predicate alone can represent a new material state and does not prove that the item is merely administrative noise.

This does not justify treating every halt as eligible. It means a hard reject on the tag alone conflicts with conservative semantic routing. The production rule should distinguish the halt notice, halt reason, resumption/update lifecycle, and nearby material issuer event.

### Bare trading-idea and short-idea predicates

The broad `trading ideas` and `short ideas` predicates were labeled `likely_eligible` because they can contain issuer-specific causal information even though the observed eligible rates are low. These channels are unsuitable standalone rejection rules. More specific template tags can still reject safely.

## Opposite-direction rejection risk

Five overlapping `big losers` predicates were blindly labeled `likely_ineligible`, but the final-period eligible rates were 44.23%-45.28% over 52-54 rows. Their Wilson lower bounds were above 5%, so this is not a small-sample zero-rate illusion.

This family must not be promoted as a deterministic rejection rule. The sharp increase is consistent with either temporal template drift or a mixed group in which some mover articles embed material causes. It requires article-level review by period before deciding whether the semantic label or current authority is wrong.

## Strong semantic/statistical agreements

Of the 100 candidates with at least 30 rows in every temporal split, 43 were labeled `likely_ineligible` and agreed with the low observed eligible rates. The clearest reusable families are:

- `bzi-pod`: recurring historical-return templates;
- `bzi-tfm`: recurring mover or sector-performance lists;
- `bzi-auoa`: recurring unusual-options-activity templates;
- explicit `top upgrades` and `top downgrades` roundup tags;
- overbought/oversold technical-screen templates.

These agreements are stronger implementation candidates than broad channels because the provider tags identify a specific generated template family. They still require exception review and a material-event rescue before production rejection.

## Interpretation and next step

The blind audit supports using Benzinga metadata, but not as one flat eligibility vocabulary. Metadata should first identify the provider template family; a semantic rescue should then preserve material issuer events. The immediate adjudication order should be:

1. Decide the product policy for direct analyst actions separately from analyst roundups.
2. Review the halt lifecycle and define which notice/update states can be rejected.
3. Investigate the 2026 `big losers` drift before using mover tags broadly.
4. Certify narrow generated-template tags such as `bzi-pod`, `bzi-tfm`, and `bzi-auoa` with blind exception samples.
5. Do not certify any of the 609 candidates lacking the forward-support floor from this analysis alone.

The blind labels are an independent semantic opinion, not corrected gold. They were produced by one isolated reviewer from the predicate and three or fewer representative examples. Any production policy change should be based on article-level adjudication of the disagreement families, not majority voting over these overlapping feature rows.

## Runtime artifacts

All row-level data and generated results are outside the repository under:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\provider_filter_candidate_blind_semantic_audit_v1`

The directory contains the frozen blind packet and manifest, all 709 blind labels, the full joined CSV, forward-supported and provisional disagreement queues, opposite-direction rejection risks, a Markdown runtime report, validation, and a complete hash manifest.
