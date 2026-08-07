# Complete the 945-news review and repair News Synthesis identity and polarity gaps

- Chat started: 2026-08-07, exact time unavailable
- Chat ended or last activity: 2026-08-07 12:36 PDT
- Summary written: 2026-08-07 12:36 PDT
- Chat/task identifier: `019fd8c9-0491-7d52-addb-34dbd167adee`
- Repository or scope: `D:\TradingCodes\quant-research-workbench`, `research/text_intelligence/news_synthesis_v1`, and external runtime audits under `D:\TradingML\runtimes\text_intelligence\news_synthesis_v1`
- Related task-history entries: `TASK-0182`
- Source completeness: Partial; the active task context, repository changes, commit, tests, and final runtime results were accessible, while portions of the long original transcript had been compacted.

### Narrative

This work continued the productionization of News Synthesis V1 after the original 1,045-label evaluation had reached direct-trading audit V15. The user required the remaining 955 labels in the 2,000-news manual authority to be converted into News Synthesis documents and manually reviewed. The governing constraints remained strict throughout: use only relevant article, metadata, and certification-code context; do not hard-code individual news cases; preserve the frozen population identities; distinguish bad gold labels from engine defects; and accept an engine repair only when an identical-population comparison showed that it fixed real errors without introducing regressions.

The conversion proceeded source by source. The remaining 945 articles requiring review were divided into narrow-context batches so reviewers received only their assigned news, metadata, and relevant certification contracts. Combined with the ten articles already completed in the conversion sequence, this brought the authority to 2,000 of 2,000 unique certified articles. Certification checks found no missing specifications, duplicate article identifiers, unresolved quality flags, or identity mismatches. The dedicated frozen manifest for the newly reviewed population is `D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\manual_conversion_v2\population_manifests\new_945.json`.

The initial evaluation of those 945 articles produced 298 eligible articles and 490 issuer units. The baseline had 253 exact sentiment matches, 237 mismatches, seven missing predictions, and zero engine failures. Investigation of the seven missing units traced them to the offline audit identity snapshot rather than the sentiment engine: securities absent from provider and legacy identity candidates lost reviewed entity identity-only fields, so the evaluator could not bind a prediction. The repair made reviewed ticker, company name, and identity evidence available only when a security was genuinely absent from the source candidates. It did not import reviewed sentiment or eligibility and did not overwrite existing source identity. This preserved the separation between evaluation authority and prediction logic.

Mismatch review also exposed several recurring polarity defects rather than case-specific exceptions. The engine confused Earnings ESP percentages with equity price movements; failed to compose analyst negation in phrases such as “no longer bullish,” “isn't a buyer,” and “not willing to recommend”; did not treat exchange minimum-bid deficiencies as adverse; allowed interim-management language to offset the death of an executive or founder too strongly; and failed to interpret an explicit absence or lack of expected cost savings as issuer-local adverse evidence. Generic rules and focused regression tests were added for these linguistic and event classes. The implementation advanced the engine to `news_synthesis_engine_v8`, the audit contract to `direct_trading_sentiment_audit_v17`, and the identity snapshot to `news_synthesis_benchmark_identity_snapshot_v3`.

Every one of the 234 mismatch Markdown packets produced after the identity repair was then reviewed manually. The review concluded that 198 gold labels were supportable by their source packets, 18 clearly contradicted their source evidence, and 18 required policy adjudication because reasonable interpretations depended on the labeling boundary. Crucially, this phase did not change either group of gold labels. The clearly wrong labels were excluded from engine tuning so the engine would not be trained toward known bad authority, but the authoritative reviewed-source correction workflow was not run. Therefore the task must not be represented as having fixed those 18 gold labels.

The 18 clearly wrong issuer labels are: `N0103/WW`, `N0303/M`, `N0905/VST`, `N0248/KLDX`, `N0248/NMKTF`, `N0480/BABA`, `N0589/SQ`, `N0744/TBPH`, `N0818/CRTD`, `N0915/TLRY`, `N1184/FEYE`, `N0086/AMZN`, `N0531/BMY`, `N0815/RDS.A`, `N0815/RDS.B`, `N1748/ALTR`, `N1748/NETL`, and `N1748/QCOM`.

The 18 policy-uncertain issuer labels are: `N0479/SFTBF`, `N0479/SFTBY`, `N1873/MNI`, `N1873/NWSA`, `N0189/CMG`, `N0657/HLF`, `N1065/GENE`, `N0045/EDSA`, `N0311/TORC`, `N0374/IMMU`, `N0477/LII`, `N0548/RL`, `N0825/RVPH`, `N1026/SMPL`, `N1473/CHTR`, `N0515/BJ`, `N0515/CSX`, and `N0530/NIO`.

Two tempting broad repairs were evaluated and rejected. A fail-closed cross-issuer fallback fixed two errors but created eight new errors on the same frozen 945 population, demonstrating that issuer roles and item relationships must be parsed structurally rather than inferred through a blanket fallback. Global mixed-sentiment or dominance-threshold tuning was also rejected because it would move unrelated cases and overfit the reviewed sample. The consolidated manual review is stored at `D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\direct_trading_sentiment_audit_v24_new_945_final\manual_mismatch_review_summary.md`.

The accepted repairs produced the final new-945 audit at `D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\direct_trading_sentiment_audit_v24_new_945_final`. Exact matches improved from 253 to 264 of 490 issuer units, mismatches fell from 237 to 226, and missing predictions fell from seven to zero. Eleven errors were fixed, no new errors appeared, no engine failures occurred, and final exact-match accuracy was 53.9 percent. The audit regenerated 226 mismatch Markdown packets.

A second regression used the complete 2,000-article certified authority and wrote results to `D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\direct_trading_sentiment_audit_v25_full_2000_final`. It contained 801 eligible articles and 1,063 issuer units. Exact matches improved from 692 to 705, mismatches fell from 371 to 358, and missing predictions fell from 15 to eight. Thirteen errors were fixed with zero newly introduced errors and zero engine failures. All eight remaining missing predictions belong to the older 1,055 tranche: `N1586/UBNT`, `N1667/MSI`, `N1673/CAR`, `N1673/HTZ`, `N1776/MNK`, `N1808/TSLA`, `N1952/CHD`, and `N1952/TEVA`.

Validation completed with 148 focused unit tests passing and `git diff --check` clean. The four production/test files were committed and pushed as `b30d7b48` (`fix: repair news synthesis identity and polarity gaps`). Generated audit packets, reports, and manifests remained under the external runtime root and were not committed to the repository.

### Durable decisions

- Confirmed requirement: repairs must model general language, event, role, identity, or aggregation behavior; article IDs and individual outcomes must never be hard-coded into production logic.
- Confirmed requirement: evaluate the identical frozen certified population and report exact matches, mismatches, missing predictions, engine failures, fixed errors, and newly introduced errors.
- Architectural decision: reviewed identity-only evidence may repair a genuinely absent offline benchmark identity, but reviewed sentiment and eligibility remain unavailable to prediction code and existing source identity cannot be overwritten.
- Architectural decision: source/gold corrections and engine changes are separate authority operations. A label identified as wrong is not corrected until its reviewed source is updated, the correction workflow is run, certified artifacts are regenerated, and the frozen audit is repeated.
- Rejected approach: broad cross-issuer fail-closed inference, because it fixed two cases but introduced eight errors. Future repair must be item- and economic-role-scoped.
- Rejected approach: global mixed/dominance threshold tuning, because the reviewed evidence did not justify moving all boundary cases.
- Unresolved uncertainty: the 18 policy-uncertain labels require explicit adjudication before either gold correction or engine tuning.

### Delivered outcomes

- Completed and certified the full 2,000-article manual authority, including source-by-source review of the remaining 945.
- Removed all seven missing predictions from the new-945 evaluation through a generic benchmark identity repair.
- Shipped generic polarity repairs and focused regression coverage in commit `b30d7b48`.
- Improved the new-945 audit by 11 exact issuer units with no new errors, and improved the full-2,000 audit by 13 exact issuer units with no new errors.
- Manually classified all 234 post-identity mismatch packets into 198 supportable, 18 clearly wrong, and 18 policy-uncertain gold outcomes.

### Unfinished or hanging work

- The 18 clearly wrong gold labels have not been corrected. Update the reviewed source specifications, run the authoritative manual-gold correction workflow, regenerate certified artifacts, and rerun the frozen 2,000 audit. Owner: News Synthesis labeling authority; related task: `TASK-0182`.
- The 18 policy-uncertain labels remain unchanged. Establish the labeling-policy decisions first, then apply only approved source-bound corrections. Owner: user and labeling authority; related task: `TASK-0182`.
- Eight older-tranche issuer units still have missing predictions. Trace each through the same identity/eligibility/prediction inventory, identify general root causes, implement only non-regressive generic repairs, and rerun the full frozen population. Owner: News Synthesis implementation; related task: `TASK-0182`.
- Historical/live processing and browser cutover validation remain incomplete after the evaluation work. Complete them only after the gold and missing-prediction authority is stable. Owner: News Synthesis productionization; related task: `TASK-0182`.

### Handoff to the next chat

Read `TASK-0182`, commit `b30d7b48`, and the V24 manual mismatch review before changing labels or engine behavior. Do not treat the 18 clearly wrong labels as corrected, do not tune against them, and do not resolve the 18 uncertain labels without an explicit policy decision. The next concrete action is to apply the reviewed-source correction workflow to the 18 clearly wrong cases, then trace the eight older missing predictions and rerun the identical 2,000-article audit. Preserve all generated outputs under `D:\TradingML\runtimes`, not in the repository.
