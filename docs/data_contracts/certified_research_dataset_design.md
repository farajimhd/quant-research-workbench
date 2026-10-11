# Certified research dataset: RL trading and strategy optimization

Design date: 2026-10-11. Status: proposed implementation contract; no campaign is authorized by this document. Target: January 2026 through the last fully certified canonical source session, with separately planned prior context. The October 11 audit resolves that endpoint to October 2, 2026 (189 exchange sessions). A later launch must resolve and freeze its endpoint again.


## The goal

Prepare one certified historical market/reference dataset for RL trading and strategy optimization. Reuse shared calculations, retain causal timing and expose consumer-ready forms. Implementation and campaigns remain separate steps.

## Page guide

| Page | What you will find |
| --- | --- |
| [Identity, timing and shared contracts](certified_research_dataset/01-foundations.md) | Approved policies, identities, clock meanings and precision. |
| [Available data, gaps and source evidence](certified_research_dataset/02-evidence.md) | Audit numbers, coverage blockers and source pointers. |
| [Acquisition, event repair and historical universe](certified_research_dataset/03-ingestion-universe.md) | Downloader, G-to-D staging, event migrations and dated ticker membership. |
| [Bars, indicators, quotes and execution data](certified_research_dataset/04-market-data.md) | Bars, indicators, quote/execution evidence and derived features. |
| [Structural V7 and session checkpoints](certified_research_dataset/05-v7-checkpoints.md) | Causal PM/regular/AH state, retrospective labels and checkpoint backfill. |
| [Fundamentals, float and short-pressure data](certified_research_dataset/06-fundamentals-short-data.md) | SEC metrics, float, short volume/interest, formulas and historical timing. |
| [RL trading inputs and required upgrades](certified_research_dataset/07-rl-inputs.md) | Sparse feature banks, teacher labels, PPO rollouts and required upgrades. |
| [Optimization inputs and required upgrades](certified_research_dataset/08-optimization-inputs.md) | Compact tapes, history banks, persistent caches and required upgrades. |
| [Execution graph and shared computation](certified_research_dataset/09-execution.md) | Runtime DAG, shared calculations, parallelism and work packages. |
| [Context planning](certified_research_dataset/09a-context.md) | Parameter-driven lookbacks, predecessor reuse and missing context. |
| [Unified launcher and recovery](certified_research_dataset/09b-launcher.md) | Launch configuration, source modes, progress, interruption and resume. |
| [Compact persistence and compression](certified_research_dataset/10-storage.md) | Compact schemas, compression, cache adapters and longer-history extension. |
| [Storage estimates and capacity admission](certified_research_dataset/10a-capacity.md) | Storage/RAM estimates, exclusions and capacity checks. |
| [Certification, delivery and open decisions](certified_research_dataset/11-certification-decisions.md) | Acceptance checks, protected splits, milestones and remaining decisions. |

## Suggested reading paths

- **Review execution:** execution graph → context → launcher.
- **Review inputs:** foundations → market data → V7 → fundamentals → your consumer.
- **Approve readiness:** evidence → storage → capacity → certification and decisions.

## Policies and remaining questions

All [approved policies](certified_research_dataset/01-foundations.md#outcome-and-approved-policies) and [open decisions](certified_research_dataset/11-certification-decisions.md#decisions-before-implementation) are preserved. The detailed pages distinguish existing capabilities from required upgrades.
