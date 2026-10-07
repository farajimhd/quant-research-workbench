"""Closed selected fixed-lot source inventory; unsealed until reviewed freeze."""
import ast
from hashlib import sha256
import json
from pathlib import Path

REQUIRED_SOURCE_FILES = (
    'src/backend/backtest_fixed_journal_bootstrap.py',
    'src/backend/backtest_trade_proposal_v3.py',
    'src/backend/backtest_typed_projection.py',
    'src/backend/backtest_typed_publisher.py',
    'src/backend/replay_run_service.py',
    'src/trading_runtime/arte_trade_proposal_children.py',
    'src/trading_runtime/arte_trade_proposal_projection.py',
    'src/trading_runtime/drawdown_measure_authority.py',
    'src/trading_runtime/drawdown_measure_policy.py',
    'src/trading_runtime/numbered_fixed_strategy.py',
    'src/trading_runtime/portfolio.py',
    'src/trading_runtime/risk_supervisor.py',
    'src/trading_runtime/runtime.py',
    'src/trading_runtime/journal_contract.py',
    'src/trading_runtime/arte_journal_projection.py',
    'src/trading_runtime/arte_journal_writer.py',
    'src/trading_runtime/arte_journal_reader.py',
    'src/trading_runtime/arte_portfolio_snapshot.py',
    'src/backend/backtest_strategy_one_configuration.py',
    'src/trading_runtime/strategy_registry.py',
    'src/backend/source_ast_summary.py',
    'src/backend/backtest_fixed_v4_certification.py',
    'src/trading_runtime/squeeze_ladder_geometry.py',
    'src/backend/backtest_declared_ladder_plan.py',
    'src/backend/backtest_ladder_source_authority.py',
    'src/trading_runtime/squeeze_ladder_automatic.py',
    'src/trading_runtime/confirmed_original_risk_failure.py',
    'src/trading_runtime/arte_original_risk_diagnostic_v4.py',
    'src/trading_runtime/original_risk_diagnostic_profile.py',
    'src/backend/backtest_confirmed_original_risk_source.py',
    'src/backend/backtest_market_data.py',
    'src/trading_runtime/original_risk_checkpoint.py',
    'src/trading_runtime/original_risk_pending_snapshot.py',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py',
    'src/trading_runtime/premarket_confirmed_original_risk.py',
    'src/backend/backtest_journal_memory.py',
    'src/backend/backtest_market_plan_cache.py',
    'src/backend/backtest_saved_source_authority.py',
    'src/backend/backtest_strategy_certified_price_break.py',
    'src/backend/backtest_strategy_first_price_source.py',
    'src/backend/backtest_strategy_initial_momentum.py',
    'src/backend/backtest_strategy_initial_momentum_growth.py',
    'src/backend/backtest_strategy_initial_price_break.py',
    'src/backend/backtest_strategy_initial_ten_percent.py',
    'src/backend/backtest_strategy_one_coordinator.py',
    'src/backend/backtest_strategy_one_execution.py',
    'src/backend/backtest_strategy_one_management.py',
    'src/backend/backtest_strategy_one_stateful.py',
    'src/backend/backtest_strategy_one_static_gate.py',
    'src/backend/backtest_strategy_one_v7_interval_store.py',
    'src/backend/backtest_strategy_rising_momentum.py',
    'src/backend/backtest_v4_history.py',
    'src/backend/backtest_v4_saved_review.py',
    'src/backend/historical_runtime_versions.py',
    'src/trading_runtime/arte_backtest_definition.py',
    'src/trading_runtime/arte_first_price_entry_v4.py',
    'src/trading_runtime/arte_followthrough_failure_v4.py',
    'src/trading_runtime/arte_initial_momentum_entry_v4.py',
    'src/trading_runtime/arte_journal_commit_v4.py',
    'src/trading_runtime/arte_journal_compound_v4.py',
    'src/trading_runtime/arte_profit_giveback_v4.py',
    'src/trading_runtime/strategy_profit_giveback.py',
    'src/trading_runtime/strategy_profit_giveback_exit.py',
    'src/trading_runtime/strategy_profit_giveback_arm.py',
    'src/trading_runtime/strategy_profit_giveback_arm_reference.py',
    'src/trading_runtime/strategy_profit_giveback_source.py',
    'src/trading_runtime/arte_rising_momentum_entry_v4.py',
    'src/trading_runtime/arte_strategy_one_entry_journal.py',
    'src/trading_runtime/strategy_followthrough_exit.py',
    'src/trading_runtime/strategy_initial_momentum_growth.py',
    'src/trading_runtime/strategy_initial_price_break.py',
    'src/trading_runtime/strategy_initial_strong_momentum.py',
    'src/trading_runtime/strategy_initial_ten_percent.py',
    'src/trading_runtime/strategy_one_campaign_snapshot.py',
    'src/trading_runtime/strategy_one_evidence_snapshot.py',
    'src/trading_runtime/strategy_one_intent.py',
    'src/trading_runtime/strategy_one_management_snapshot.py',
    'src/trading_runtime/strategy_one_oms_observation_snapshot.py',
    'src/trading_runtime/strategy_persistent_risk_failure.py',
    'src/trading_runtime/strategy_premarket_quarter_risk_failure.py',
    'src/trading_runtime/strategy_rising_momentum_entry.py',
    'src/trading_runtime/strategy_rising_momentum_witness.py',
    'src/trading_runtime/strategy_strong_ten_second_momentum.py',
    'src/trading_runtime/strategy_thirty_release.py',
    'src/trading_runtime/strategy_twenty_eight_release.py',
    'src/trading_runtime/strategy_twenty_five_release.py',
    'src/trading_runtime/strategy_twenty_four_release.py',
    'src/trading_runtime/strategy_twenty_nine_release.py',
    'src/trading_runtime/strategy_twenty_one_release.py',
    'src/trading_runtime/strategy_twenty_release.py',
    'src/trading_runtime/strategy_twenty_seven_release.py',
    'src/trading_runtime/strategy_twenty_six_release.py',
    'src/trading_runtime/strategy_twenty_three_release.py',
    'src/trading_runtime/strategy_twenty_two_release.py',
    'src/trading_runtime/strategy_zero_regime_risk_failure.py',
    'src/trading_runtime/entry_momentum_growth.py',
    'src/backend/backtest_declared_initial_momentum.py',
    'src/backend/backtest_saved_writer_source.py',
    'src/backend/backtest_saved_reader_certification.py',
    'pipelines/strategy_one/configuration_publisher.py',
    'pipelines/strategy_one/strategy_thirty_one_configuration.py',
    'src/trading_runtime/arte_journal_rowbinary.py',
    'src/trading_runtime/arte_typed_insert_dispatch.py',
    'research/mlops/clickhouse.py',
    'src/trading_runtime/arte_oms_projection.py',
    'src/trading_runtime/arte_profit_giveback_reader_v4.py',
    'src/trading_runtime/strategy_thirty_one_release.py',
    'src/trading_runtime/strategy_thirty_two_release.py',
    'pipelines/strategy_one/strategy_thirty_two_configuration.py',
    'src/trading_runtime/order_management.py',
    'src/trading_runtime/strategy_thirty_three_release.py',
    'pipelines/strategy_one/strategy_thirty_three_configuration.py',
    'src/trading_runtime/strategy_half_risk_liquidity_fade.py',
    'src/backend/backtest_strategy_half_risk_liquidity_fade.py',
    'scripts/clickhouse/publish_strategy_thirty_five_configuration.py',
    'src/trading_runtime/strategy_confirmed_ah_failure_source.py',
    'src/trading_runtime/arte_confirmed_ah_failure_v4.py',
    'src/trading_runtime/strategy_confirmed_ah_failure_exit.py',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py',
    'src/backend/backtest_strategy_liquidity_fade.py',
    'src/trading_runtime/strategy_liquidity_fade_failure.py',
    'src/trading_runtime/strategy_liquidity_fade_exit.py',
    'src/trading_runtime/arte_liquidity_fade_failure_v4.py',
    'src/trading_runtime/strategy_liquidity_fade_source.py',
    'src/trading_runtime/strategy_liquidity_fade_market_source.py',
    'src/trading_runtime/strategy_thirty_five_release.py',
    'pipelines/strategy_one/strategy_thirty_five_configuration.py',
    'src/trading_runtime/strategy_liquidity_fade_transport.py',
    'src/backend/backtest_strategy_liquidity_fade_loader.py',
    'src/trading_runtime/strategy_liquidity_fade_entry_source.py',
    'src/backend/backtest_strategy_liquidity_fade_decision.py',
    'src/backend/backtest_strategy_one_financial.py',
    'src/trading_runtime/arte_liquidity_fade_reader_v4.py',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint.py',
    'src/trading_runtime/arte_intent_projection.py',
    'src/trading_runtime/strategy_liquidity_fade_publication.py',
    'scripts/clickhouse/publish_strategy_thirty_six_configuration.py',
    'src/trading_runtime/strategy_entry_activity_fade.py',
    'src/trading_runtime/strategy_entry_activity_witness.py',
    'src/trading_runtime/arte_entry_activity_v4.py',
    'src/backend/backtest_strategy_episode_activity_source.py',
    'src/backend/backtest_strategy_episode_activity_gate.py',
    'src/trading_runtime/strategy_episode_activity_veto.py',
    'src/backend/backtest_strategy_entry_activity_gate.py',
    'src/backend/backtest_strategy_entry_activity_source.py',
    'src/trading_runtime/strategy_thirty_six_release.py',
    'pipelines/strategy_one/strategy_thirty_six_configuration.py',
    'src/trading_runtime/strategy_thirty_seven_release.py',
    'pipelines/strategy_one/strategy_thirty_seven_configuration.py',
    'scripts/clickhouse/publish_strategy_thirty_seven_configuration.py',
    'src/trading_runtime/strategy_forty_two_release.py',
    'pipelines/strategy_one/strategy_forty_two_configuration.py',
    'scripts/clickhouse/publish_strategy_forty_two_configuration.py',
    'src/backend/backtest_fixed_structural_lot_native.py',
    'src/backend/backtest_fixed_structural_lot_source.py',
    'src/backend/backtest_fixed_structural_lot_management.py',
    'src/trading_runtime/fixed_structural_lot_release.py',
    'src/trading_runtime/fixed_structural_lot_policy.py',
    'src/trading_runtime/fixed_structural_lot_entry.py',
    'src/trading_runtime/fixed_structural_lot_entry_schema.py',
    'src/trading_runtime/fixed_structural_lot_entry_v4.py',
    'src/trading_runtime/fixed_structural_lot_state.py',
    'src/trading_runtime/fixed_structural_lot_management.py',
    'src/trading_runtime/fixed_structural_lot_snapshot.py',
    'src/trading_runtime/independent_lot_stop_amendment.py',
    'src/trading_runtime/independent_lot_protection.py',
    'src/backend/backtest_review_loading.py',
    'src/trading_runtime/broker.py',
    'src/trading_runtime/clickhouse_transport.py',
    'src/trading_runtime/strategy_one_configuration_tree.py',
    'src/trading_runtime/entry_spread_risk.py',
    'src/trading_runtime/signals.py',
    'src/trading_runtime/strategy_one_stateful.py',
    'src/trading_runtime/strategy_one_protection_intent.py',
    'src/trading_runtime/early_squeeze_breakout.py',
    'src/trading_runtime/early_squeeze_price.py',
    'src/trading_runtime/execution_policies.py',
    'src/trading_runtime/strategy_one_contract.py',
    'src/trading_runtime/strategy_one_v7_intervals.py',
    'src/trading_runtime/arte_journal_schema.py',
    'src/trading_runtime/strategy_one_position.py',
    'src/trading_runtime/strategy_one_protection_snapshot.py',
    'src/trading_runtime/squeeze_ladder_lots.py',
    'src/trading_runtime/ibkr_schema.py',
    'src/trading_runtime/fixed_structural_lot_contract.py',
    'src/trading_runtime/strategy_seventy_seven_release.py',
    'src/trading_runtime/strategy_seventy_seven_contract.py',
    'pipelines/strategy_one/strategy_seventy_seven_configuration.py',
    'src/trading_runtime/fixed_structural_lot_profile.py',
    'scripts/clickhouse/provision_backtest_v4_fixed_structural_lot_runner.py',
    'scripts/clickhouse/provision_backtest_v4_runner.py',
    'scripts/clickhouse/publish_strategy_seventy_seven_configuration.py',
    'scripts/clickhouse/install_market_day_certificate_layout.py',
    'scripts/clickhouse/provision_strategy_one_configuration_publisher.py',
    'src/trading_runtime/keeper_session.py',
    'scripts/clickhouse/smoke_strategy_one_backtest.py',
    'src/backend/backtest_v3_clients.py',
    'scripts/clickhouse/publish_strategy_one_configuration.py',
    'src/backend/backtest_fixed_structural_lot_configuration.py',
    'src/trading_runtime/fixed_structural_lot_manager_schema.py',
    'src/trading_runtime/arte_protection_deferral_v4.py',
    'src/trading_runtime/arte_protection_reconciliation_v4.py',
    'src/backend/backtest_fixed_structural_lot_empty.py',
    'src/backend/backtest_fixed_structural_lot_execution.py',
    'src/trading_runtime/fixed_structural_lot_cold_recovery.py',
    'src/trading_runtime/fixed_structural_lot_manager_snapshot.py',
    'src/backend/backtest_v4_broker_state_restore.py',
    'src/backend/backtest_v4_broker_quote_restore.py',
    'src/backend/backtest_v4_execution_restore.py',
    'src/backend/backtest_v4_running_recovery.py',
    'src/trading_runtime/arte_oms_actor_restore.py',
    'src/trading_runtime/arte_portfolio_recovery.py',
    'src/trading_runtime/strategy_engine.py',
    'src/request_context.py',
    'scripts/clickhouse/provision_fixed_backtest_v3_principals.py',
    'scripts/clickhouse/provision_trading_journal.py',
    'src/backend/backtest_strategy_one_candidate_store.py',
    'src/backend/backtest_input_scope.py',
    'src/trading_runtime/keeper_ownership.py',
    'src/backend/backtest_fixed_running_anchor.py',
    'src/backend/backtest_fixed_structural_lot_resume.py',
    'src/backend/backtest_v4_running_portfolio.py',
)
REVIEWED_SOURCE_AST = {'src/backend/backtest_fixed_journal_bootstrap.py': 'b18b9d515ff54426268fe06c36b49ad373b05b77a802c6d36668eaa90ba2974c', 'src/backend/backtest_trade_proposal_v3.py': '105c0861f4f64ca8a472a6df5eab6b757672c2d372a486eea381b25a26bfb986', 'src/backend/backtest_typed_projection.py': 'db2a934fcdb129be532cc125dcedcfa785ed6bb360d22312b59da67e65a8f029', 'src/backend/backtest_typed_publisher.py': 'd045d14d2f8afc924d5790d821042111967ce51d741f5bb707e521ffa5627502', 'src/backend/replay_run_service.py': '3f58e3d819c13175385fb8ece9dac62e32c01edc98781d9ad051aff707fcfd77', 'src/trading_runtime/arte_trade_proposal_children.py': 'c306edb3cdf3ac0a1dd0935c15b305fed4e52736794cbaac353d86245ed41272', 'src/trading_runtime/arte_trade_proposal_projection.py': '3db1d832a3f703a011c97d399e7cc725143201955674fd8d2aba61b796c11d55', 'src/trading_runtime/drawdown_measure_authority.py': '5c3e9281d3ef17eeeb9e75e9847b30de5619aef598b85c2d0e687ed2befbf87d', 'src/trading_runtime/drawdown_measure_policy.py': '8db141b2fd01030439e5c6c9a4040a6a3462183ae7575abfe3fcd17c1f7d8f96', 'src/trading_runtime/numbered_fixed_strategy.py': 'a645a80b8639ff65cab7d82aa44a6be487b3ca8bec2814690c13e59c4db4af39', 'src/trading_runtime/portfolio.py': 'd14c31fd8d4c392f2f98c748b401a0b50042524a4d0b56a586b059e19056bf8f', 'src/trading_runtime/risk_supervisor.py': '412c0d2406acd76d49771b562fea96bec2a1331b8a1cc4e6cd483d99c4fbd975', 'src/trading_runtime/runtime.py': '51db1e7f1ede6820ce8bea130bc19f4185fece58ac16b800cb0564d1be654145', 'src/trading_runtime/journal_contract.py': '130e8b70f3708cd30f0524b035a0c99cded407bf6b1fc39a610cb7e19458da28', 'src/trading_runtime/arte_journal_projection.py': '9c78a31a6bad5fb1026053f0b5552c9ed37bf717c75143da25f14d9df3e08efc', 'src/trading_runtime/arte_journal_writer.py': '1a80fc2783e85224cfc1d40893941d3d1954094126fabfe7bae795a85cac6b81', 'src/trading_runtime/arte_journal_reader.py': 'c99c2bae8b6647c5ea1f4185ea7dab38a807b821b040eb90e1a12b9949860ae2', 'src/trading_runtime/arte_portfolio_snapshot.py': '6a66afd31ac6665364929c40e293f6a11e8559165d9a46fbc852f69b0b55ea8e', 'src/backend/backtest_strategy_one_configuration.py': '09382990291d52787e56f84eb9dc5537c44fa1e70f6782cc0f30cede602c9e6f', 'src/trading_runtime/strategy_registry.py': 'fc22fb3a2ee54037b4b9243b4464a9d6d72b7b35f52f321b07318779e23dc278', 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995', 'src/backend/backtest_fixed_v4_certification.py': '7eec7f7482ca3b9b396f7c0a3ec55f380171c8d5dfc6a67657130738124a45c5', 'src/trading_runtime/squeeze_ladder_geometry.py': '34658cac7dba475b19ca3963bbcad1397a88f5f86e4133664947c9e656671e0d', 'src/backend/backtest_declared_ladder_plan.py': 'a6db11ddef4ccfe9707fc60bf0e998c106eda12c84634b8caf49b2ff4e1373cb', 'src/backend/backtest_ladder_source_authority.py': '02e9ad68a84b32da0770ba0c68d1a4fca0cc3747e78537a48da2aeb7f5d75c52', 'src/trading_runtime/squeeze_ladder_automatic.py': '0474d98c356ea6999afe132e3295fa6e703d87d5227f7fe0186d6213e9b5b91d', 'src/trading_runtime/confirmed_original_risk_failure.py': '738493dce849c08fd7e1d4f5c24a7c3188bc772a183d9083a86713177aba6f39', 'src/trading_runtime/arte_original_risk_diagnostic_v4.py': '747bd14c7cd27adc1151770c7b946d6c14074a2e813f167dcf7e4241604176d2', 'src/trading_runtime/original_risk_diagnostic_profile.py': '595f15675892f4b9f59cb1ee1b19536e59ab47ca563f55971b39811614a4298c', 'src/backend/backtest_confirmed_original_risk_source.py': 'f51caf0f2197a606d02de3972aaeb41b9291dd487a2549fe2fd9987162bfa859', 'src/backend/backtest_market_data.py': 'ad4135f16a0979b5af821b0c00c294511635b3b84b37b90f7bdff79e503cf9d3', 'src/trading_runtime/original_risk_checkpoint.py': '9049359bdf28ddbbbfdebeaa31cf48dfe4fd035cae561869f0a1e831ed4a1cee', 'src/trading_runtime/original_risk_pending_snapshot.py': '0c18cf677683921ff30f41c6b5dde78a43e5ad190e34bdde572baeabb14d9cac', 'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'a6011032b9690f0ddb72e8db54401e57e7850431e243023f3c3b8d90d524643c', 'src/trading_runtime/strategy_one_broker_match_snapshot.py': '4dff4622888b967be79d4c1a1ece64e124153e56a812b37bea3a3c72a8469672', 'src/trading_runtime/premarket_confirmed_original_risk.py': '907931a13a9e71e9b7daf96ae15be0e51f356b9c246fc153a7a234bb1475a7fb', 'src/backend/backtest_journal_memory.py': 'b9a2d0b1e1091f99835a018e519a7ea11ae5c169ab1d65fd7472e87f6ddf9f4e', 'src/backend/backtest_market_plan_cache.py': '083a9ff687ec93721265a453c19f0d338953c13f812053796df625c2a200a2b1', 'src/backend/backtest_saved_source_authority.py': 'b0f17ce10cdfdbaf09315e64899dff9c6ef87b0fd520bfa334649401bcafd6bf', 'src/backend/backtest_strategy_certified_price_break.py': 'e1fa545fbbd95b4784e4cf2e0ef320276a4ff525683f680ddea6fc0ccd4c1a38', 'src/backend/backtest_strategy_first_price_source.py': 'a2557a2973a3ad1371fd9d97ffe740cf0687aa510372b1d6471d0284f92d2146', 'src/backend/backtest_strategy_initial_momentum.py': '954446cab169240a183801637b4f61b27f90d45467b065ef428760d1ed5a48df', 'src/backend/backtest_strategy_initial_momentum_growth.py': '33bf35371d2216a5361e735959cdd1e48a65be3a5d8f04ec3d79cd277d199485', 'src/backend/backtest_strategy_initial_price_break.py': 'aa5968f100749c56e97c66279ba4bd1feb583e9b3a8f8a978718f234b2d98ff6', 'src/backend/backtest_strategy_initial_ten_percent.py': '73b8b04654cd8ebef2a8906908bac4c82fa06616fb4d50d19f73442f7f07f420', 'src/backend/backtest_strategy_one_coordinator.py': '7d40c9a3d75872e1ff263729eb578cf9860e913bae3863ec8889ca999e5df334', 'src/backend/backtest_strategy_one_execution.py': '55beb538a169d525540792c488d8fc0f566a5e911802d498d2e461ee40f684dc', 'src/backend/backtest_strategy_one_management.py': 'c29f6e43ffd8ee4732395b372f07cd28c38c59899766d3311c4090eed63c9061', 'src/backend/backtest_strategy_one_stateful.py': 'fec2d6e1dbe5913f1e3d8b856e58f73e018687e642ffa906dd647a534f46703b', 'src/backend/backtest_strategy_one_static_gate.py': '202419d71a3b064a7216a6dcb8032993d1c8310fe90e7a37890b287c4a02a986', 'src/backend/backtest_strategy_one_v7_interval_store.py': 'ea26c1cdda7bc028c0394354d9f5baf5b8f0124a7bc0761f4c9e242c58699548', 'src/backend/backtest_strategy_rising_momentum.py': 'c0f4a1084b29088a1df63cdeb5c1b82b3d467fe2a657ee08ab37037bd6a6f245', 'src/backend/backtest_v4_history.py': '9ed3f860d27d2b6f776d4b42c0e9225fc84a75744504fa9dce60dcdd1326bd23', 'src/backend/backtest_v4_saved_review.py': 'f6a2048ea195b7a39e6694e89cb7f164e4d83826197727ee90dceefb5e852ce9', 'src/backend/historical_runtime_versions.py': '96941bdcb6b84439c3ba3f0d1fc0deb9cde238746088d1b284284aa0969857ec', 'src/trading_runtime/arte_backtest_definition.py': '70a3b6031bff897ccf9ae6e33e09c449898bea558f1dce05e512ac0875d7df5f', 'src/trading_runtime/arte_first_price_entry_v4.py': 'c968e50cb42d4326f9499b20134ec6500990058a6415aed581f373fb2d2efec6', 'src/trading_runtime/arte_followthrough_failure_v4.py': '2ce1e6805a9db71fa5afe0ec63aba61a7d87fc95e3847b89bd7e94b93aa1bc6c', 'src/trading_runtime/arte_initial_momentum_entry_v4.py': '037069b752fbc7af9de87ba44f53d3af79833cdb379872ce2554013456af21ea', 'src/trading_runtime/arte_journal_commit_v4.py': 'c59c3be0f654c37509d9787d37b7d56e7c2c84f0b24bc65926a3b2befa99609e', 'src/trading_runtime/arte_journal_compound_v4.py': '05b28c131df86e9b743117c1c801eec7384995b0bbe8c8b80c9afb53e183eb4c', 'src/trading_runtime/arte_profit_giveback_v4.py': '3e22f8f0c57d85a471cd15a63e18295517c617f9b8dc88eae5d1cc7908a70293', 'src/trading_runtime/strategy_profit_giveback.py': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10', 'src/trading_runtime/strategy_profit_giveback_exit.py': 'c8466273babe33b6c54ed898d446a6b1b68297fd2e37e69b27270a4bc804b49e', 'src/trading_runtime/strategy_profit_giveback_arm.py': 'ef5b2892793ecf7ef2d6957a0851c1c2010e1d4da5c21c7e0dbc5de46fc39f41', 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6', 'src/trading_runtime/strategy_profit_giveback_source.py': 'b3e58e1bf6cbb1f77a79ddfd41a8ce7115c634dc80753877549f37947db75189', 'src/trading_runtime/arte_rising_momentum_entry_v4.py': '2f9b2689448c0592d99134286955618d0c312c29d8712d37f79310bffbb55d1d', 'src/trading_runtime/arte_strategy_one_entry_journal.py': '45dc2b6abf7f528fbf1e056c5249a78eb1b52813f051a732512712abc5993856', 'src/trading_runtime/strategy_followthrough_exit.py': 'e5ed9aa87f606bed29e550756d11e7fa1445d9ffb47f69d4f1402bfdb17b9984', 'src/trading_runtime/strategy_initial_momentum_growth.py': '68d66854b639e67a5d3734aaaf1a61ce015963d6841746a59c4ad85390761606', 'src/trading_runtime/strategy_initial_price_break.py': '5279377acee015b28239ecd1949657fb1ce66731835a6190b686bf54a38ce3ce', 'src/trading_runtime/strategy_initial_strong_momentum.py': '65c6021a7a7c287682a502989fe03b638ee6aaa6ae1488e9a323a0e4c75f4c06', 'src/trading_runtime/strategy_initial_ten_percent.py': '086a330212aa01c2ddf70bdb8ddb2db70654f4b851662977ea86ec307be18be2', 'src/trading_runtime/strategy_one_campaign_snapshot.py': 'eddcf4cd8d92fffba34302f3c4121bb33f08b85789b3cc1d106c945faa52341b', 'src/trading_runtime/strategy_one_evidence_snapshot.py': 'f8cd2ae1b550f656b4d156a5c780bfaaee2a30ae0f829e40558771fad30dd74c', 'src/trading_runtime/strategy_one_intent.py': '55258f2be65ba5485276a54e3384d8bd2cdb32dc39a66520e0f0cb98bc2ae97b', 'src/trading_runtime/strategy_one_management_snapshot.py': '91b14c0b13f11d15cb0e34bf14a7b1e88f752d893e9e7a4b95af215c0f39478a', 'src/trading_runtime/strategy_one_oms_observation_snapshot.py': '07797b380214432dec2009abd133bd37331be1372351f7f5c2ffac86e1552fb2', 'src/trading_runtime/strategy_persistent_risk_failure.py': '62bbdbcc400315b7c63a3b3260dbaae0247cc128cadd26969075deacd61a3e52', 'src/trading_runtime/strategy_premarket_quarter_risk_failure.py': '87a7e1941a30e09f0e3b463187d9914f8389186a528d9f706ec76b5e6ea529b9', 'src/trading_runtime/strategy_rising_momentum_entry.py': '26f5e82b33a5e7e4126fd703d9748ca3ee14b3696df4f6b9eddb79db96e05ea7', 'src/trading_runtime/strategy_rising_momentum_witness.py': '8433d7be44361854221822708c23dbbc7899b106a16aa5d9f62d2e1825d6bf30', 'src/trading_runtime/strategy_strong_ten_second_momentum.py': '70071f8696a3675e4a7344328d65327a84b539cb7e03f09c4fae49542a74dba5', 'src/trading_runtime/strategy_thirty_release.py': '049384140302bd83801e7b16447263c1847aa9ff7d2a4853bea5eb10aad84ea2', 'src/trading_runtime/strategy_twenty_eight_release.py': '0cccc9b6115abd744a09d760482f35a317f0d1f795b70fd580a7c9c1730fe6a8', 'src/trading_runtime/strategy_twenty_five_release.py': '1bbba29e4542af0fbe42ef7d07ebe852926039910c02d0680d1ad429def83f3a', 'src/trading_runtime/strategy_twenty_four_release.py': '7913d809cbe160b2d2018c63f4749ee2dd085bc4a1f6fa8412dde9c5168f5859', 'src/trading_runtime/strategy_twenty_nine_release.py': '58f134b9c6f17f5448d536a00779cddf32a4a43d172edeed0137fe02e348e0e7', 'src/trading_runtime/strategy_twenty_one_release.py': '0255b201419a032c712acf7de0fb5c45f1fe78d502d47b64c4c247031ab35679', 'src/trading_runtime/strategy_twenty_release.py': 'ec84c03c9e2b5d314be5b77204fa27904285123db8c54563459fa19a9724d281', 'src/trading_runtime/strategy_twenty_seven_release.py': '25b56cd9b46abc53eda393573a8a0ceca991e9037eb6869e0ab17b51192d2348', 'src/trading_runtime/strategy_twenty_six_release.py': '7344cb7fcdcb6cd3f6e10aea1400fe4b8e9e721e11294eab402d63576e901440', 'src/trading_runtime/strategy_twenty_three_release.py': 'e43951d61064e390078f9ac25d78eca0c000e0d33b28c54e663b71f644511c4f', 'src/trading_runtime/strategy_twenty_two_release.py': 'dfd5e4ccca3a285d278649a3374412e24638e29e62f57cf62f3db64c902d830f', 'src/trading_runtime/strategy_zero_regime_risk_failure.py': '2cca428561093253b73728c7c52a41b9e91dcf622f28fdd7ca93bb66660e8cd1', 'src/trading_runtime/entry_momentum_growth.py': '8acec288e9cf7d1c62bf8cb2fa386f82d5db69a63fc9306b90168f8cee3c0b5c', 'src/backend/backtest_declared_initial_momentum.py': '786a12c62a157c092652c8cd7d695fe4f5e8ebfb315c9b8dbab56580fb0a4b81', 'src/backend/backtest_saved_writer_source.py': '7d1ce4767a5a48ad5ef9bab611f9cb5632e1aaac0c9673ba6b52c5e0350d0d25', 'src/backend/backtest_saved_reader_certification.py': '12515f1c3d8d6a211403dc7a3ae5705c3caae0a0e7bc6c561d8d229b227cdb70', 'pipelines/strategy_one/configuration_publisher.py': '433cfe48ec94f539760284fb425381a52f79a655f93813216bc8b65e0f4ebe31', 'pipelines/strategy_one/strategy_thirty_one_configuration.py': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515', 'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49', 'src/trading_runtime/arte_typed_insert_dispatch.py': '429b4a0d32b035f264e8dd67324f81ae54f7ccd243055eeee6c0b34806662d43', 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8', 'src/trading_runtime/arte_oms_projection.py': '5d6895c8cf740b4dd64586c8777d427a32b0334ca887ce392671a0aa6a04f516', 'src/trading_runtime/arte_profit_giveback_reader_v4.py': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3', 'src/trading_runtime/strategy_thirty_one_release.py': '2fcd0c7c34073032bf96e2007a901bd8fa99d120d6290a904e13a4e2c94f290e', 'src/trading_runtime/strategy_thirty_two_release.py': '66a79fcdd60af4cbf2b7d217340d7b8efa95576ffe694ae05aa597cdd61361a9', 'pipelines/strategy_one/strategy_thirty_two_configuration.py': '13a485fb66cdbfab61270b391441d2bb9613c65be77dcfed68eac0494428db7c', 'src/trading_runtime/order_management.py': '2c6d028018e2756b6295640f5e61fc71797d3120ef7c9062561cf258a742596e', 'src/trading_runtime/strategy_thirty_three_release.py': 'cc0afd9027402150723a87f1e66526c7acf35aa78f113d1d5ec57b5675cd386e', 'pipelines/strategy_one/strategy_thirty_three_configuration.py': '20dbac5cf28f3f61409c187a9dfcc34b43da02eb81a161ce45ad70f53a4d060c', 'src/trading_runtime/strategy_half_risk_liquidity_fade.py': '3f8d8949125090eda81d3256cae65920f950da12cbbb88dc6fb30a922c4cbe21', 'src/backend/backtest_strategy_half_risk_liquidity_fade.py': '266015bf39f14014f1705a8729499b472b8803b5ad5d7619b9f69677f5ed0718', 'scripts/clickhouse/publish_strategy_thirty_five_configuration.py': '4ea3528baf3efb8b60699af4bb9ee81b5c693c01360912dc86d7a2691fba547b', 'src/trading_runtime/strategy_confirmed_ah_failure_source.py': '5b5fbaeda54d8e7593d6646c7d6631ab8323ee472cdf73ecee947e049a77b50a', 'src/trading_runtime/arte_confirmed_ah_failure_v4.py': '5f8b90176e9bbe6232a1f7b88a86333cc2466dc5a932740d27b7cbf4d4345435', 'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '5fce50ffc270e19c98f200dbe949edd9ce718959d8f7cb48324dce5f1e96590f', 'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py': '2bf2d32ba8e66f95d6646c3f364937dd342fe25af9cd3311632e50bf958369d8', 'src/backend/backtest_strategy_liquidity_fade.py': '4870509aaef5af9a6b45d7a9c389dfe737a927f0beb71164b00ef9919d1a7e24', 'src/trading_runtime/strategy_liquidity_fade_failure.py': '25ec4e6e986c8cd684740d9140ac5b659b36f386170d3bad6ce2af2e5569350e', 'src/trading_runtime/strategy_liquidity_fade_exit.py': '3ccbc3b5f09d6553a23372e9a2c435c936defa2dc800717d36f2824c21a04325', 'src/trading_runtime/arte_liquidity_fade_failure_v4.py': '216e413631d81e7cb0b29d7103ab1939320946a43a6efeba17c646cdc6147134', 'src/trading_runtime/strategy_liquidity_fade_source.py': '5554b994c4ac8c566a1883c676c3c01206ee9d091ba0e10dc2c7c81890f126b2', 'src/trading_runtime/strategy_liquidity_fade_market_source.py': 'fde06254872bf85500bafa0564d68753c6a908665539b7f3cbb3a7a296083127', 'src/trading_runtime/strategy_thirty_five_release.py': 'f0e45aba520f8792f65b375929d39063107a209b9b458c559be043d2fe64cfcd', 'pipelines/strategy_one/strategy_thirty_five_configuration.py': '21bfe1c3c954a7535bf49c3b6a1a0b94cab0f1693c8f8bd0e3e8511856184abe', 'src/trading_runtime/strategy_liquidity_fade_transport.py': 'd3801bf25d21f372be32000aaef5b5f9a86ba168c37a40a5e9afe0f836184dfe', 'src/backend/backtest_strategy_liquidity_fade_loader.py': 'd6e5660d40bb75df739f8fe862ac7a8ed8441cdd7c58da64184f219d5d8a62b4', 'src/trading_runtime/strategy_liquidity_fade_entry_source.py': 'b411bf1a156f61414df8b765eb3f3606d98f79c9c541d781ef86329949238ef0', 'src/backend/backtest_strategy_liquidity_fade_decision.py': '7aaf49f043a2c30da30ed3a24303e27e80082c7d5b0af510aac06ee1ed68a1b0', 'src/backend/backtest_strategy_one_financial.py': 'f184a0f434aabb3895b1323d2a22056acb30b4ab9bf301cb8356b1eea2591f0d', 'src/trading_runtime/arte_liquidity_fade_reader_v4.py': 'de0080bdf30bae6cb32105ee52d369f6769cf472888ccacdf6bba974b4648acf', 'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53', 'src/trading_runtime/arte_intent_projection.py': 'b53b0251cd632d6a314632b59c0c1523fa035cc4e19058a4398287d9020ff082', 'src/trading_runtime/strategy_liquidity_fade_publication.py': '3ae4e11e8b2d2d9f470c4beab7c0af09b0259c1168a367f728778ed8accb6827', 'scripts/clickhouse/publish_strategy_thirty_six_configuration.py': '9181d082d60d5269e13b69448febc5100b955ff42a9a9acbce0735f28bfd71a9', 'src/trading_runtime/strategy_entry_activity_fade.py': '78219596eedb2c3de41ed0597f4e5bde5efea9d68854645f2786ad53fa5c4b75', 'src/trading_runtime/strategy_entry_activity_witness.py': 'ce4bfd96579271bd7d7124e700c40824e9c870266ff05d9ea10b50bcb4d72bc8', 'src/trading_runtime/arte_entry_activity_v4.py': '95b32e75b49992cb38e0569e1576e093841f46eb3b8defd37e5ec4d0e8494b92', 'src/backend/backtest_strategy_episode_activity_source.py': '2d4fa40c46a866be83328ce7443a350655620bf9c4b4a82044dc45ee7441c8c0', 'src/backend/backtest_strategy_episode_activity_gate.py': '43eaa3e95701f32ab02f5af501ca0e019b03ed91badb8f1bcb62ced3918510b5', 'src/trading_runtime/strategy_episode_activity_veto.py': 'cb96806e4594bdb2b087b1c12925ee80d296e1313984dce6329f3eee4b911e06', 'src/backend/backtest_strategy_entry_activity_gate.py': 'a128c7856cbc77854871961ec4d582b5f44b9bdeee517f6dde61360811fd02d4', 'src/backend/backtest_strategy_entry_activity_source.py': 'db6e9f16dd62c48f98dc5ec8f7bfa40a17f69974bb618097418ec3f246d91681', 'src/trading_runtime/strategy_thirty_six_release.py': '9a243bfe59b4071497bac22231ac0fb104262af40ff282e4910e6819a8b29308', 'pipelines/strategy_one/strategy_thirty_six_configuration.py': '4a03259d1ab4a0ae29a2a14672fdef94249cac0b614478817b9f22d8a91d34b6', 'src/trading_runtime/strategy_thirty_seven_release.py': '6966c161682e06421a8a0112980a3a487ac4485ca9d2d86700d6621ef3687892', 'pipelines/strategy_one/strategy_thirty_seven_configuration.py': 'a4af45a5b3a4caca5de7eeee6f56cde807013fee3ec1f4e462e0a0f9027c29fa', 'scripts/clickhouse/publish_strategy_thirty_seven_configuration.py': 'd7489b4edacd0a96aaf3521f189151398a832e3d60c04c5f402d4652c7ee2825', 'src/trading_runtime/strategy_forty_two_release.py': 'ae2e532ddaf0adc3b40ad77a40f8d3fd9cbd6154c344671a17e66545cc81656f', 'pipelines/strategy_one/strategy_forty_two_configuration.py': '7c046aa96f8789a5626b564c25891375e0481e0ccb342a1c7e0f1019268db40c', 'scripts/clickhouse/publish_strategy_forty_two_configuration.py': 'a6f006f9f593640c949d74bbb8a918ecbc9dae8390567076ae54aa1c4ebdc8ea', 'src/backend/backtest_fixed_structural_lot_native.py': '434e61312731974232e0fdc51eff33917046fe46e08781ee30934ea30d1ca773', 'src/backend/backtest_fixed_structural_lot_source.py': '93a5357e59eace6f9176449f6b5999a963fdd3fec29b908ff6b14814964b3b4b', 'src/backend/backtest_fixed_structural_lot_management.py': '3a02ad5175ac808f74ab77279284f629c5d33b9a0ed643676909bb6270d005e7', 'src/trading_runtime/fixed_structural_lot_release.py': '4736654f112c80552b0b9ac585cb93cafde69a3446513971043e6e9a148002d0', 'src/trading_runtime/fixed_structural_lot_policy.py': 'be83ceab828b4d9e8315c2b114fd40058f4dae6376e9bcd8afd52b9caafc8e33', 'src/trading_runtime/fixed_structural_lot_entry.py': '871e16d6693e03923c9b70a8b900e0a8853cd84f18da206face6c26929ac96be', 'src/trading_runtime/fixed_structural_lot_entry_schema.py': '45ad17075f1275b01325e81a57c6dbd8caa6e870bc242754eaaa0aca9a6095bb', 'src/trading_runtime/fixed_structural_lot_entry_v4.py': 'f19d960bb6e59c4fae15860a59e50da2f1101685d3ea0d33d5034375556f86ba', 'src/trading_runtime/fixed_structural_lot_state.py': '522b597aa5ac7df073ce10b350f6d5797b98cb37ad40056b87e237431d3f6b7e', 'src/trading_runtime/fixed_structural_lot_management.py': 'cf56d956224402484257d09b069bd80e194351f275c3ab0dfcce068721e87240', 'src/trading_runtime/fixed_structural_lot_snapshot.py': 'd370ea494b90888e83b578228ddb04a879cd9617e4d98c10b8f0e4020bdf5c81', 'src/trading_runtime/independent_lot_stop_amendment.py': '1098dd0488bf5314a336d656bcce329bf963c2ed413ff00826c2c0956dd01a4b', 'src/trading_runtime/independent_lot_protection.py': '498b88e02d483d3b8c8c327b874045df3946a80cb2cc02ac472737e1ccdd36af', 'src/backend/backtest_review_loading.py': 'd4b0ecad6f57cbf6b9548d9fea736535e758c229a7e09ca9196596ecd905e28e', 'src/trading_runtime/broker.py': '67cbe0a8bb2f06b2dcae1ed2d59a8e0dbb83d5766d766355722062cf505b317a', 'src/trading_runtime/clickhouse_transport.py': '108e4e9a4e757ed792ab01f8d7faefdc035b2da398cfcbdd8dc3917c20cbb5df', 'src/trading_runtime/strategy_one_configuration_tree.py': '44f46197d4075bbdae4d39c7f4b8160fc6ae4895e11a90f13db927f4268b7e06', 'src/trading_runtime/entry_spread_risk.py': 'cbb5fcbae0b6c6581a3dd9a2d59dd6dbc57372799f2781e3d69c4559b5a6be8e', 'src/trading_runtime/signals.py': 'cb686ca71a6c6c1e72c8f6df1fc16c0bae03c5b0dde627e57c7bef07598408d3', 'src/trading_runtime/strategy_one_stateful.py': 'f3c065f73502c8d3b1971b01e0e16359dd3dca969f33ffce70be0d6c36d1f86b', 'src/trading_runtime/strategy_one_protection_intent.py': '4c465f78bc91cb8fcf2a8ae578427c00d787e70a6577a5e09c7d524bddf75892', 'src/trading_runtime/early_squeeze_breakout.py': 'cb49631c6c2e95f65e0bcdc2ba3b45178f4c33733377caaa16eb12e600cad296', 'src/trading_runtime/early_squeeze_price.py': 'd128df9d9484c72d0983f4111fd9969e1ca3f51eec6fc02f8e16831a6b46f378', 'src/trading_runtime/execution_policies.py': 'c0766986efe8afc2d3276e2cbb1f303a4abb99427724f2ee2c1d0a29e4e2f36c', 'src/trading_runtime/strategy_one_contract.py': '18cec6086f5e6cc45fe68d2be6c1e04d729ef092d92a9d527f2830de0d18d277', 'src/trading_runtime/strategy_one_v7_intervals.py': '86ceddef8a39a1850123268a57b635abd8ade55aa0fa765236d4d97801a100c1', 'src/trading_runtime/arte_journal_schema.py': '3d194bbbac0dc8628ac296925fcf3ea42ff9fef1140f516584b72fbc9e0d6a5c', 'src/trading_runtime/strategy_one_position.py': 'b9cb6b6d1ee78860e310a4d7e69c136bd4dcc2bad5c1c68e9789c40587e816a7', 'src/trading_runtime/strategy_one_protection_snapshot.py': '6a085872857962c9de47bec1d872181aae62490df34898bf14b12618a5458940', 'src/trading_runtime/squeeze_ladder_lots.py': '0b31d6955210ec0e28f6411f2b921e465046fbda21b101da0cca12f02efc61b2', 'src/trading_runtime/ibkr_schema.py': '925fb38783932105171da76a75c16dc092f7c7a4dcddb39d3882781a8cdf1fe3', 'src/trading_runtime/fixed_structural_lot_contract.py': 'e21c0d6db8495d8aa6c4dc0344713e02f506fc1fe04d0762f32b5fe6b9dcfdb3', 'src/trading_runtime/strategy_seventy_seven_release.py': '23f974cd0c79dd698f911f09887c545d3e61ddf94f7d3ca2f4e0c0f8bac00146', 'src/trading_runtime/strategy_seventy_seven_contract.py': '3a231a7755411908c40dbef4614ae1b8e8d85f14e2176a3b959ab471c7a83c3d', 'pipelines/strategy_one/strategy_seventy_seven_configuration.py': '287b4a53ae3237265d8fd27c7d5f0e4286b53c7194e9f08d774d3207529f10cd', 'src/trading_runtime/fixed_structural_lot_profile.py': '9e693ba1b1626b59e80a0e251ed2020b3e206cd00182f08a859690afd54e11e0', 'scripts/clickhouse/provision_backtest_v4_fixed_structural_lot_runner.py': '388adfddab15c31dafbbbfd73f822fedc2f893aada12a86363ecc1a52031e4da', 'scripts/clickhouse/provision_backtest_v4_runner.py': 'a97b2323c1104b0c81396cc9ad5999d5962ce44bffc8617916ff9f67993fe056', 'scripts/clickhouse/publish_strategy_seventy_seven_configuration.py': '13b362f43d6c77f0dcba0b4df9c19bf0ef9b6740df23a608c51e9ed6d6694963', 'scripts/clickhouse/install_market_day_certificate_layout.py': '03b0d60f602a74315c852f5a8eef0eb8d09e4d2f079bd7495825efa23410524e', 'scripts/clickhouse/provision_strategy_one_configuration_publisher.py': 'cf3ba903cbdc0ad225b7f070b2eb9cbd4eef74e466eb7ea0f5f9acac901695af', 'src/trading_runtime/keeper_session.py': '316240cc8dde1b4047d618efc89fc8e5bd281770e4db5d9c1267f2147fb06a18', 'scripts/clickhouse/smoke_strategy_one_backtest.py': '9fd5c419469518490823ca1c18032cb43fa42aacab38d545be14112f93fa292f', 'src/backend/backtest_v3_clients.py': '3e992adf1ea531e89b33371196ac01b9d813235e451228f9d831f4ce1d656bdd', 'scripts/clickhouse/publish_strategy_one_configuration.py': '3074a145ebea8bbacb8fef44e3b35801b268f759ab2947ac1e9fbe50f30fa9c7', 'src/backend/backtest_fixed_structural_lot_configuration.py': 'dba5d8f8afff6666787adda15568c5d0557a1ca4c16c4770799e2fa2558ecff0', 'src/trading_runtime/fixed_structural_lot_manager_schema.py': '85e554babd3af346dcbecfd805e5ade22e80219110746109780d9e56d5e8db36', 'src/trading_runtime/arte_protection_deferral_v4.py': '4a909a76a8a5e7d3cdd6002d7fd1b989f7ed815bd1d1238e1cf4aad69b5fe736', 'src/trading_runtime/arte_protection_reconciliation_v4.py': 'aa9885b850875ca8b2aca5ec0df6745d63dc4302719cf2d5e20745874d568547', 'src/backend/backtest_fixed_structural_lot_empty.py': '74ea749a7ccd498da570d6e59a6dd461f8d92ac877aeecca84b488cba78e66e0', 'src/backend/backtest_fixed_structural_lot_execution.py': '41124c6eb9069e6ceb2a2127fd69d5c50e0ee68bf37272a70c239a6f89373aae', 'src/trading_runtime/fixed_structural_lot_cold_recovery.py': 'cb398119237c572afb6e8528e4ad083b39e845c97c831f2c7c4012bb50eaff09', 'src/trading_runtime/fixed_structural_lot_manager_snapshot.py': '4683837a953557b25848b3b0b6950ea67d924797edec0a409484a64495fc1024', 'src/backend/backtest_v4_broker_state_restore.py': '14f6e8d0d1b4511bb9acdf53b7a4e24709cf1aea517752cf7456b1e5c1d714e9', 'src/backend/backtest_v4_broker_quote_restore.py': '12dda882ea1f5d6272f208839e3240494e0a5c82033fb94115a39c05ddad26a5', 'src/backend/backtest_v4_execution_restore.py': '44ceb684574a7b1b5f9e4f1f9663d713cad3abea9903718342328fb82e6b6864', 'src/backend/backtest_v4_running_recovery.py': '5c26a395fc4da11d39a70028a7b02dad860f4cafd65f2d0bb93844c06f138955', 'src/trading_runtime/arte_oms_actor_restore.py': '2bc61b5daf38b5dbda43385be01206415658059639fcd3d286ae599ccad4a1b5', 'src/trading_runtime/arte_portfolio_recovery.py': '0a308ffb074febb404803448c2ee8beedb4374114d9d653fc423215d8e46a1b5', 'src/trading_runtime/strategy_engine.py': '48f5a2379095297984334ac046dabd8e36c8c3288434852f8454aed52cc8eaa6', 'src/request_context.py': 'a01bdd1cac96a31401f7f7bde55acab9220acfb37a42f0988e2cfd0f6fc8530c', 'scripts/clickhouse/provision_fixed_backtest_v3_principals.py': '77df1f30c5fb6fb5700f2b8843a2d71a0f801beb27026371678666e5d228a6ee', 'scripts/clickhouse/provision_trading_journal.py': 'fb0a05f5a3098bcd28482db4b7f34dc3a47e3ac436bca0b0ca747262dd1e507e', 'src/backend/backtest_strategy_one_candidate_store.py': 'b523e2e125344abe221cdb630608f43c3620f650ba25ee530eb058e2d8fd073c', 'src/backend/backtest_input_scope.py': '6c3f60809879eb7f3c8b710fac42944212c0111b9964dccb6062b544b78725b1', 'src/trading_runtime/keeper_ownership.py': '4d50a7acea06377df65ab79c506685c8ce426d1981e867049341c93e3047c2f7', 'src/backend/backtest_fixed_running_anchor.py': '93a2fca87f2b5122ec7d580488cd8cb87c620b08c6afe181cdec767b095ce1b0', 'src/backend/backtest_fixed_structural_lot_resume.py': '12b4f92c029bb1c22fcadbae2b54d0df0a887b5324593113db46f82a520ca276', 'src/backend/backtest_v4_running_portfolio.py': '6ea39f1a837374072568646ecd99f10447e12b0ea9669c0a403028d7ffbddac8'}
APPROVED_METADATA_ANCHOR = 'ce411e4dc9ec3843fa57ff8832f8de637a9b6f561c16f09719669603f6c5bd35'
APPROVED_SELF_AST = 'a922beb1a71120c2eccf225a9a05362ad124ecca2403df254de6876fefa6ab2a'


def certify_fixed_structural_lot_source() -> str:
    """Issue no source authority from caller hashes or an unreviewed inventory."""
    root = Path(__file__).resolve().parents[2]
    own = root / 'src/backend/backtest_fixed_structural_lot_certification.py'
    try:
        own_source = own.read_text(encoding='utf-8')
        tree = ast.parse(own_source)
    except (OSError, SyntaxError) as exc:
        raise ValueError('Fixed-lot source certifier cannot be read') from exc
    names = ('REQUIRED_SOURCE_FILES', 'REVIEWED_SOURCE_AST',
             'APPROVED_METADATA_ANCHOR', 'APPROVED_SELF_AST')
    if (len(tree.body) != 10 or not isinstance(tree.body[0], ast.Expr)
            or not isinstance(tree.body[0].value, ast.Constant)
            or tree.body[0].value.value != __doc__
            or [ast.unparse(n) for n in tree.body[1:5]] !=
               ['import ast', 'from hashlib import sha256', 'import json', 'from pathlib import Path']
            or any(type(n) is not ast.Assign or len(n.targets) != 1
                   or type(n.targets[0]) is not ast.Name or n.targets[0].id != name
                   for n, name in zip(tree.body[5:9], names))
            or type(tree.body[9]) is not ast.FunctionDef
            or tree.body[9].name != 'certify_fixed_structural_lot_source'
            or tree.body[9].decorator_list
            or tree.body[9].args.posonlyargs or tree.body[9].args.args
            or tree.body[9].args.kwonlyargs or tree.body[9].args.vararg
            or tree.body[9].args.kwarg):
        raise ValueError('Fixed-lot certifier module envelope differs')
    try:
        fresh = {name: ast.literal_eval(n.value)
                 for name, n in zip(names, tree.body[5:9])}
    except (ValueError, TypeError) as exc:
        raise ValueError('Fixed-lot certifier declarations are not literal') from exc
    loaded = dict(zip(names, (REQUIRED_SOURCE_FILES, REVIEWED_SOURCE_AST,
                             APPROVED_METADATA_ANCHOR, APPROVED_SELF_AST)))
    if fresh != loaded:
        raise ValueError('Fixed-lot loaded and fresh declarations differ')
    required = REQUIRED_SOURCE_FILES
    if (type(required) is not tuple or not required
            or any(type(p) is not str or not p.startswith(('src/', 'pipelines/', 'scripts/', 'research/'))
                   or '..' in p.split('/') or '\\' in p or not p.endswith('.py') for p in required)
            or len(set(required)) != len(required)):
        raise ValueError('Fixed-lot required source inventory is malformed')
    if type(REVIEWED_SOURCE_AST) is not dict:
        raise ValueError('Fixed-lot reviewed source pins must be an exact map')
    if not REVIEWED_SOURCE_AST and not APPROVED_METADATA_ANCHOR and not APPROVED_SELF_AST:
        raise ValueError('Fixed-lot source seal is unapproved; admission remains closed')
    if tuple(REVIEWED_SOURCE_AST) != required:
        raise ValueError('Fixed-lot reviewed source keyset or order differs')
    pins = (*REVIEWED_SOURCE_AST.values(), APPROVED_METADATA_ANCHOR, APPROVED_SELF_AST)
    if any(type(v) is not str or len(v) != 64 or any(c not in '0123456789abcdef' for c in v) for v in pins):
        raise ValueError('Fixed-lot source pins are malformed')
    metadata = {'required': required, 'sources': REVIEWED_SOURCE_AST}
    if sha256(json.dumps(metadata, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != APPROVED_METADATA_ANCHOR:
        raise ValueError('Fixed-lot source metadata anchor differs')
    if sha256(ast.unparse(tree.body[9]).encode()).hexdigest() != APPROVED_SELF_AST:
        raise ValueError('Fixed-lot certifier function source differs')
    observed = [('certifier_function', APPROVED_SELF_AST),
                ('certifier_metadata', APPROVED_METADATA_ANCHOR)]
    sealed_sources = []
    for relative in required:
        path = root / relative
        if not path.is_file() or path.is_symlink() or root not in path.resolve().parents:
            raise ValueError('Fixed-lot required source path is missing or foreign: ' + relative)
        try:
            source = path.read_text(encoding='utf-8')
            selected = ast.parse(source)
        except (OSError, SyntaxError) as exc:
            raise ValueError('Fixed-lot required source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(selected).encode()).hexdigest() != REVIEWED_SOURCE_AST[relative]:
            raise ValueError('Fixed-lot reviewed source authority changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
        sealed_sources.append((path, source))
    if any(path.read_text(encoding='utf-8') != source for path, source in sealed_sources):
        raise ValueError('Fixed-lot source changed during certification')
    if own.read_text(encoding='utf-8') != own_source:
        raise ValueError('Fixed-lot certifier changed during certification')
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
