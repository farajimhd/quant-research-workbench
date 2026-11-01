# V61: bottom-up strategy evolution

Self-contained copy of V60 at `1ef2df3c9`, with identical market inputs, execution, score and limits. Full campaigns remain stopped pending review.

## Founder construction

6144 fresh founders: 3072 simple market rules (1-3 nodes per action), 1536 combinations (3-7), and 1536 compact templates balanced across the existing families. Random leaf predicates use one feature; other nodes have at most three initial inputs unless required by their tree. Templates lose redundant output identity nodes without truncating rules. The training-only activation probe is reused because its market and execution contract is unchanged. No V60 genomes or fitness seed V61.

The random component retains V60 policy ranges and joint feasibility projection. Calibration can repair saturated graphs or replace failed proposals; these are recorded. Every founder slot must be valid and unique. Templates remain ordinary evolvable graphs.

## Evolution

20% of reproduction's crossover/mutation slots request local growth: identity insertion, confirming predicate, or alternative predicate. Identity insertion preserves the parent's action and sizing outputs exactly. Condition/alternative operators combine margins around the parent's threshold. Inapplicable mutations retry through legal structural edits, and report what actually occurred. Full batches receive replacements for exhausted construction attempts.

Parent selection retains 25% ticker-specialist and 75% global-ranked requests. Ten percent of requests choose a rotating logarithmic size bucket inside that chosen rank pool. Equal objective scores prefer smaller candidates; financial scores are unchanged. Existing routing, veto, entry/management exchange and crossover remain, with complete dependency-preserving subtree exchange added. All branches count against the same 1024-node total budget and 16-input node limit. There is no recurrent or position feedback.

Generation telemetry includes node distribution, requested/accepted growth, mutation deltas, operator outcomes, parent comparisons and survival. Existing observer quality and method-group pages remain available; no full-run observer is launched during qualification.

## Qualification

Use the production `profile_global` path with a fresh source-bound job and `--mixed --count 1024 --reproduce` for a bounded two-session replay and one CPU reproduction. Mixed sampling preserves 50/25/25 without selecting by financial results. Compare against V60 fresh founders using the same training dates, candidate count, execution settings and objective. This is initial-quality evidence, not a generalization claim or authorization to run 200 generations.
