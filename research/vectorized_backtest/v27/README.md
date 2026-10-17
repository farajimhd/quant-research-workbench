# V27: V21 baseline with extended crossover

V27 is a separate experiment based on the current V21 128-node baseline. It does not stop, resume, migrate or modify V26. Implementation does not authorize a campaign launch or install an automatic queue.

## Preserved V21 behavior

- Local score: `P - 0.25 W - 0.05 D`, where P is full fee-adjusted net profit, W is positive-only fractional top-10%-traded-ticker profit removal, and D is session equity drawdown.
- Global score: `sum(P) - 0.25 max(sum(W), W_session) - 0.05 sum(D)`. W_session removes the positive-only fractional top 10% of training-session profits. Losses remain fully counted. Display profit ranking and reproduction score remain distinct.
- Same random initialization and seeds, 6x initial local population, subsequent population 1,024, 30 local generations and 50 global generations. The chronological 24/12 dataset, fees, causal rules, $1,000 entries/$200 additions, 0..20 additions, 20% maximum stop distance and $300 planned position-risk ceiling remain unchanged.
- Same 102 unchanged elites, 205 fresh immigrants and 717 mating slots at population 1,024. First-parent routes remain 359 score-tournament / 215 uniform / 143 elite slots, with five-link ancestry exclusion and duplicate rejection.
- V21's 20..85% mutation mask applies to all non-survivor categories, including immigrants; unchanged elites are excluded. Plain candidates retain the original 1..5 distinct effective edits. There is no additional V26-style 15% mutation-only replacement of mating slots, and crossover children do not silently receive a one-edit cap. Routed children can mutate a routing choice or a leaf; leaf edits use the V21 editor.
- V21's profit-band local archives and training-selected, display-only validation shortlist remain unchanged. Validation does not influence parent selection, similarity, operator probabilities, mutation or RNG.

## Unified crossover

Each mating follows: original first-parent route → ancestry-valid partner pool → behavioral partner request → composition operator → existing duplicate/validity checks → original mutation mask → pedigree publication.

The default operator request weights are **50% `v21_legacy`**, with the other **50% uniformly divided among 13 extensions**:

| Family | Operators | Effect |
|---|---|---|
| Original V21 | `v21_legacy` | Independently select lifecycle rules, exchange one typed subtree, recombine policy genes and ages using the exact V21 implementation. |
| Rule/parameter operations | `subtree`, `lifecycle`, `parameters` | Exchange a typed subtree, coherent lifecycle blocks, or policy parameters. |
| Logical operations | `union`, `intersection`, `difference`, `conditional_rule` | OR, AND, A AND NOT B, or condition-based rule choice. |
| Whole-strategy routing | `priority`, `reverse_priority`, `conditional`, `agreement`, `exclusive`, `overlay` | Choose complete strategy branches or use agreement/veto behavior. |

Each rule retains the 128-node limit. Routers have two non-routed leaves, one routing layer, and a total executable-node budget of 1,408 including the optional condition. When composing routed parents, a leaf is chosen before rule operations. On entry, the selected branch owns that position until it closes; a later routing change cannot seize it. Overlay may request exit or veto an addition, but cannot increase exposure. Entry eligibility still passes the original financial risk checks.

Requested weights are not guarantees of accepted offspring proportions: invalid compositions, effective duplicates and bounded retries can change acceptance. Generation reports retain operator attempts, rejections and accepted counts; each birth records its operator, requested/effective behavior route, parent serials, similarity and mutations.

## Training-only behavioral pairing

Similar, dissimilar and random partner requests each have probability one third. Only the second-parent choice changes; first-parent route budgets and ancestry restrictions remain V21's.

One BehaviorIndex is built from training position ledgers for each reproduction event. Training replay records entry/exit clocks during its existing evaluation; no separate overlap replay is performed. Final generations and V21-control runs do not build an unused index. Phase 2 combines all training-session ledgers; validation ledgers are never supplied.

Positions are sparse intervals in disjoint session/listing namespaces. A bounded 256-bit endpoint sketch screens at most 32 proposed partners, then at most four exact interval comparisons select a similar/dissimilar partner. Exact score is the mean of occupancy Jaccard and one-to-one entry-match Jaccard (60-second tolerance). Shared holding duration and directional coverage are also recorded. An 8,192-pair cache avoids repeated exact queries; no dense population-by-time or all-pairs matrix is allocated. Empty exposure does not count as maximum dissimilarity; fallbacks and their effective random route remain visible. Random pairs receive a cached exact diagnostic when both have positions.

This is the V26 breeding similarity definition, **not** the later post-Phase-2 full occupancy report. Selecting on validation overlap remains forbidden.

## Exact control, resources and source contracts

`--v21-control` disables behavioral pairing and uses 100% original V21 crossover. Tests compare initial payloads, offspring, policy mutations, RNG, reproduction budgets and numerical objectives against V21. Selection and recombination settings are saved in controller, local and global contracts; changing them cannot silently resume a checkpoint.

Compatible routing support and V26's compact-node/gate-write compute improvements are carried forward without adopting V26's pure-profit objective or altered mutation schedule. Two host-session slots, no speculative prefetch and a 128 GiB reserve plus 32 GiB admission allowance protect the parallel bars workload. This is a soft admission guard, not an OS-enforced memory cap. At most two workers are admitted. Full-session CUDA throughput has not been qualified for V27.

`certified_sources.py` binds raw hashes from the certified V21 `df9d81af2-r2` deployment. Per-directory Git attributes preserve the five certified source files byte-for-byte, including their mixed line endings. Other source identity checks remain in force; certificates are not weakened. Existing V21/V26 files and evidence are untouched.

## Entry points

Use `python -B -m research.vectorized_backtest.v27.run_train --help` for complete arguments. As in V21, `--check-only` is read-only readiness, `--authorize-run` is required for compute, and a completed certified V19 predecessor is required. The launch gate also refuses competing V7..V26 optimizers. Both phases share the same V27 reproduction path. A full launch requires the real pinned training/validation manifests, immutable source commit, predecessor job and a new output below `D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v27`.

Do not reuse an old campaign's checkpoint, import evolved descendants as random initialization, or launch while V26 is active. Generation-one inputs can be reconstructed identically from the V21 seed; this implementation evaluates them normally to record training position clocks. No historical fitness or result cache is imported.
