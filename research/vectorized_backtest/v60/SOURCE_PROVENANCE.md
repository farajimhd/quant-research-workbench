# V60 source provenance

Initial source baseline: `518e7c2bb`; V59 audit baseline: `99fb181db`.

The V59 panel, controller, sparse neural evaluator and financial ledgers were copied into V60. They are local source, not runtime dependencies. V60 semantic changes and acceptance gates are listed in README.md and the strategy/execution audit.

| V60 module | Reviewed source |
|---|---|
| `__init__.py` | `v59/__init__.py` |
| `allocation.py` | `v59/allocation.py` |
| `behavior_sketch.py` | `v59/behavior_sketch.py` |
| `blind_mix.py` | `v59/blind_mix.py` |
| `calibration.py` | `v59/calibration.py` |
| `campaign_contract.py` | `v59/campaign_contract.py` |
| `campaign_export.py` | `v59/campaign_export.py` |
| `campaign_launch.py` | `v59/campaign_launch.py` |
| `campaign_progress.py` | `v59/campaign_progress.py` |
| `campaign_resume.py` | `v59/campaign_resume.py` |
| `campaign_runner.py` | `v59/campaign_runner.py` |
| `campaign_seed.py` | `v59/campaign_seed.py` |
| `capture_lifecycle.py` | `v59/capture_lifecycle.py` |
| `completed_replays.py` | `v59/completed_replays.py` |
| `composition.py` | `v59/composition.py` |
| `config.py` | `v59/config.py` |
| `controller_metrics.py` | `v59/controller_metrics.py` |
| `cpu_work.py` | `v59/cpu_work.py` |
| `data.py` | `v59/data.py` |
| `distributed_preparation.py` | `v59/distributed_preparation.py` |
| `entry_sharing.py` | `v59/entry_sharing.py` |
| `episode_scatter.py` | `v59/episode_scatter.py` |
| `episodes.py` | `v59/episodes.py` |
| `evaluator.py` | `v59/evaluator.py` |
| `evolution_report.py` | `v59/evolution_report.py` |
| `execution_restart.py` | `v59/execution_restart.py` |
| `feature_policy.py` | `v59/feature_policy.py` |
| `financial.py` | `v59/financial.py` |
| `full_common.py` | `v59/full_common.py` |
| `full_controller.py` | `v59/full_controller.py` |
| `full_launch.py` | `v59/full_launch.py` |
| `full_progress.py` | `v59/full_progress.py` |
| `full_seed.py` | `v59/full_seed.py` |
| `generation_flow.py` | `v59/generation_flow.py` |
| `generation_pipeline.py` | `v59/generation_pipeline.py` |
| `genome.py` | `v59/genome.py` |
| `global_contract.py` | `v59/global_contract.py` |
| `global_runner.py` | `v59/global_runner.py` |
| `gpu_gate.py` | `v59/gpu_gate.py` |
| `identity.py` | `v59/identity.py` |
| `identity_cache.py` | `v59/identity_cache.py` |
| `immigration.py` | `v59/immigration.py` |
| `immigration_cache.py` | `v59/immigration_cache.py` |
| `initial_population.py` | `v59/initial_population.py` |
| `initial_progress.py` | `v59/initial_progress.py` |
| `initial_reuse.py` | `v59/initial_reuse.py` |
| `lineage.py` | `v59/lineage.py` |
| `local_runner.py` | `v59/local_runner.py` |
| `mask_genome.py` | `v59/mask_genome.py` |
| `mutation_probe.py` | `v59/mutation_probe.py` |
| `network.py` | `v59/network.py` |
| `network_cuda.py` | `v59/network_cuda.py` |
| `network_pack.py` | `v59/network_pack.py` |
| `objectives.py` | `v59/objectives.py` |
| `observer_io.py` | `v59/observer_io.py` |
| `offspring_audit.py` | `v59/offspring_audit.py` |
| `operators.py` | `v59/operators.py` |
| `order_accounting.py` | `v59/order_accounting.py` |
| `order_accounting_cuda.py` | `v59/order_accounting_cuda.py` |
| `order_details.py` | `v59/order_details.py` |
| `order_journal.py` | `v59/order_journal.py` |
| `order_liquidity.py` | `v59/order_liquidity.py` |
| `order_liquidity_cuda.py` | `v59/order_liquidity_cuda.py` |
| `order_records.py` | `v59/order_records.py` |
| `order_replay.py` | `v59/order_replay.py` |
| `order_scatter.py` | `v59/order_scatter.py` |
| `order_step.py` | `v59/order_step.py` |
| `order_tail.py` | `v59/order_tail.py` |
| `order_transition.py` | `v59/order_transition.py` |
| `orders.py` | `v59/orders.py` |
| `ownership.py` | `v59/ownership.py` |
| `paired_replay.py` | `v59/paired_replay.py` |
| `pilot.py` | `v59/pilot.py` |
| `population_snapshot.py` | `v59/population_snapshot.py` |
| `position_details.py` | `v59/position_details.py` |
| `prefetch_pipeline.py` | `v59/prefetch_pipeline.py` |
| `prepare_execution.py` | `v59/prepare_execution.py` |
| `profile_concentration.py` | `v59/profile_concentration.py` |
| `profile_generation.py` | `v59/profile_generation.py` |
| `profile_global.py` | `v59/profile_global.py` |
| `profile_replay.py` | `v59/profile_replay.py` |
| `progress.py` | `v59/progress.py` |
| `progress_archive.py` | `v59/progress_archive.py` |
| `progress_global.py` | `v59/progress_global.py` |
| `progress_sessions.py` | `v59/progress_sessions.py` |
| `protected_transition.py` | `v59/protected_transition.py` |
| `publication_lane.py` | `v59/publication_lane.py` |
| `random_population.py` | `v59/random_population.py` |
| `replay_batches.py` | `v59/replay_batches.py` |
| `replay_graph.py` | `v59/replay_graph.py` |
| `reproduction_prefetch.py` | `v59/reproduction_prefetch.py` |
| `resume_authority.py` | `v59/resume_authority.py` |
| `robust_score.py` | `v59/robust_score.py` |
| `routing_plan.py` | `v59/routing_plan.py` |
| `run_train.py` | `v59/run_train.py` |
| `runtime_path.py` | `v59/runtime_path.py` |
| `sampling_report.py` | `v59/sampling_report.py` |
| `scheduled_launch.py` | `v59/scheduled_launch.py` |
| `session_lanes.py` | `v59/session_lanes.py` |
| `session_pipeline.py` | `v59/session_pipeline.py` |
| `species.py` | `v59/species.py` |
| `strategy_mix.py` | `v59/strategy_mix.py` |
| `swing_projection.py` | `v59/swing_projection.py` |
| `templates.py` | `v59/templates.py` |
| `ticker_selection.py` | `v59/ticker_selection.py` |
| `validation_cache.py` | `v59/validation_cache.py` |
| `whole_network.py` | `v59/whole_network.py` |

Additional locally owned primitives were extracted from V42 schedule/publication/distribution, certified data/history readers, continuous indicator math and feature projection; V27 policy/feature projection; V7 fixed terminal and feature-tile reader; and V6 runtime/receipt/ownership helpers. Source-data protocol names retain their original versions where the certified bytes and mathematics are unchanged. They do not select an older implementation.

No campaign is qualified solely by a successful import or kernel microbenchmark. Full isolated replay, reproduction, data contracts and workstation throughput remain acceptance requirements.
