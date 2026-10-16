# V22: V20 selection and objectives with V21 rule capabilities

V22 is an isolated V20 copy for a bundled rule-capability ablation. No campaign is started by creating this version. V20 and V21 source and running campaigns are unchanged.

## Experimental contract

Retained from V20: all local/global objective coefficients and performance eligibility gates; parent routes, elitism, crossover and mutation eligibility/rates; parameter-distance archives and shortlist sizes; training-selected validation shortlist; 1,024 initial and subsequent candidates; 30 local and 50 global generations by default; five-link ancestry, exact dedup, hard execution/risk checks, fees, causal inputs and validation independence. Complexity price was already zero in V20 and remains zero.

Ported from V21: balanced requested rule-size bins 3..9 / 10..19 / 20..32; broader typed arithmetic/temporal/comparison sampling (including cross-above/below); explicit same-unit division and uncentered x/LAG(x,h) ratios; 50/50 uniform/log-magnitude numeric priors and ratio thresholds; movement/close instead of movement/close-1; maximum_adds 0..20; sampling coverage reports. The hard per-rule limit is now 128 nodes; the 120-observed-candle context limit remains. Archive descriptor addition scaling changes from /5 to /20 to match the new legal gene range; archive selection algorithm and weights remain V20's.

Not ported: V21 score-only eligibility, new objectives, random-parent or elite-pair routes, mutation of fresh candidates, profit-band reservoirs, score/profit validation union, or sixfold initialization. Original feature producers/certificates remain unchanged; V22 projections use the explicit v22-magnitude-ratio-v1 identity in controller, local and global contracts. Source hashes bind the isolated V22 tree and inherited v6/v7 dependencies. Checkpoints from other versions cannot resume as V22.

## Interpretation

V22 versus V20 measures the combined rule/feature/action/sampling changes under V20 selection and costs. V22 versus V21 additionally differs in initialization effort, archives, mutation eligibility and validation shortlist; it does not isolate the cost function alone. Equal seeds do not create identical populations after sampler changes. Freeze dataset, execution assumptions and generation/evaluation budgets when comparing; compare training-selected candidates on a common frozen evaluation panel rather than cherry-picking different validation shortlists. No profitability conclusion follows from implementation tests.

## Running and evidence

Use the V20-compatible controller: python -B -u -m research.vectorized_backtest.v22.run_train --help. Supply explicit --training, --validation, --output, --predecessor-job and --commit. Generated output must be beneath D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v22. The inherited gate requires the pinned completed V19 predecessor and its exact dataset proof; it is a readiness gate, not candidate import. --check-only performs readiness checks without launching compute. --mode full is required for a full campaign; the inherited default profile mode runs a short local profile. No scheduled launch or workstation sync is installed by this change.

The CPU regression suite is tests/test_vector_v22.py. It covers retained financial/objective/selection behavior, local restart/RNG/lineage/seals, archive seeding, actual CPU worker replay/cache publication, completion/ownership gates, and the ported feature/sampling changes. CUDA throughput and full campaign results require separate runtime validation. Existing hard limits remain $1,000 entries, $200 additions, 20% stop distance and $300 planned position risk.

The 128-node increase is user-authorized. Random-rule sampling bands remain 3..9 / 10..19 / 20..32; crossover may grow larger rules. Controller, local and global contracts bind maximum_rule_nodes=128. Archive descriptors allocate 128 node slots and normalize links by 127; V20 archive selection algorithm and weights remain unchanged. Existing 32-node checkpoints cannot silently resume under this source/contract.
