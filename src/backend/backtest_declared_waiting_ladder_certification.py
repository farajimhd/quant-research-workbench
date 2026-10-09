"""Separate declared waiting-ladder source review; unresolved reviews fail closed.

This closure does not register, publish, or approve financial/native execution.
Existing waiting-baseline and selected-exit certificates remain unchanged.
"""
import ast
from hashlib import sha256
import json
from pathlib import Path
REQUIRED_SOURCE_FILES = ('pipelines/strategy_one/configuration_publisher.py',
 'pipelines/strategy_one/strategy_sixty_five_configuration.py',
 'scripts/clickhouse/provision_backtest_v4_ladder_runner.py',
 'scripts/clickhouse/provision_backtest_v4_waiting_ladder_runner.py',
 'scripts/clickhouse/publish_strategy_sixty_five_configuration.py',
 'scripts/clickhouse/report_strategy_one_trades.py',
 'src/backend/backtest_declared_initial_momentum.py',
 'src/backend/backtest_declared_ladder_plan.py',
 'src/backend/backtest_declared_ladder_seed.py',
 'src/backend/backtest_fixed_journal_bootstrap.py',
 'src/backend/backtest_journal_memory.py',
 'src/backend/backtest_ladder_coordinator.py',
 'src/backend/backtest_ladder_entry_authority.py',
 'src/backend/backtest_ladder_source_authority.py',
 'src/backend/backtest_squeeze_ladder_admission.py',
 'src/backend/backtest_squeeze_ladder_entry.py',
 'src/backend/backtest_squeeze_ladder_evidence.py',
 'src/backend/backtest_squeeze_ladder_journal_admission.py',
 'src/backend/backtest_squeeze_ladder_loader.py',
 'src/backend/backtest_squeeze_ladder_readback.py',
 'src/backend/backtest_squeeze_ladder_setup.py',
 'src/backend/backtest_strategy_one_configuration.py',
 'src/backend/backtest_strategy_one_coordinator.py',
 'src/backend/backtest_strategy_one_execution.py',
 'src/backend/backtest_typed_projection.py',
 'src/backend/backtest_typed_publisher.py',
 'src/backend/backtest_v4_saved_review.py',
 'src/backend/replay_run_service.py',
 'src/backend/source_ast_summary.py',
 'src/data_provider/calendar.py',
 'src/trading_runtime/arte_backtest_snapshot_anchor.py',
 'src/trading_runtime/arte_journal_commit_v4.py',
 'src/trading_runtime/arte_journal_writer.py',
 'src/trading_runtime/arte_oms_projection.py',
 'src/trading_runtime/arte_squeeze_ladder_schema.py',
 'src/trading_runtime/automatic_ladder_transport.py',
 'src/trading_runtime/entry_momentum_growth.py',
 'src/trading_runtime/execution_policies.py',
 'src/trading_runtime/independent_lot_protection.py',
 'src/trading_runtime/numbered_fixed_strategy.py',
 'src/trading_runtime/order_management.py',
 'src/trading_runtime/portfolio.py',
 'src/trading_runtime/portfolio_config.py',
 'src/trading_runtime/runtime.py',
 'src/trading_runtime/session_acquisition_admission.py',
 'src/trading_runtime/squeeze_ladder_admission.py',
 'src/trading_runtime/squeeze_ladder_automatic.py',
 'src/trading_runtime/squeeze_ladder_columnar.py',
 'src/trading_runtime/squeeze_ladder_cross.py',
 'src/trading_runtime/squeeze_ladder_geometry.py',
 'src/trading_runtime/squeeze_ladder_lots.py',
 'src/trading_runtime/squeeze_ladder_protection.py',
 'src/trading_runtime/squeeze_ladder_setup.py',
 'src/trading_runtime/strategy_orders.py',
 'src/trading_runtime/strategy_registry.py',
 'src/trading_runtime/strategy_sixty_five_contract.py',
 'src/trading_runtime/strategy_sixty_five_release.py',
 'src/backend/backtest_market_data.py',
 'src/backend/backtest_fixed_v4_certification.py',
 'src/backend/backtest_fixed_run_context.py',
 'src/backend/backtest_fixed_v3_preflight.py',
 'src/backend/backtest_v4_keeper_lease.py',
 'src/backend/backtest_saved_source_authority.py',
 'src/backend/typed_backtest_review_core.py',
 'src/trading_runtime/arte_backtest_definition.py',
 'src/trading_runtime/arte_journal_schema.py',
 'src/trading_runtime/arte_journal_reader.py',
 'src/trading_runtime/arte_journal_projection.py',
 'src/trading_runtime/arte_typed_insert_dispatch.py',
 'src/trading_runtime/journal_contract.py',
 'src/trading_runtime/strategy_one_configuration_tree.py',
 'src/trading_runtime/strategy_engine.py',
 'src/trading_runtime/keeper_session.py',
 'src/trading_runtime/keeper_ownership.py',
 'scripts/clickhouse/provision_backtest_v4_runner.py',
 'scripts/clickhouse/provision_fixed_backtest_v3_principals.py',
 'scripts/clickhouse/provision_trading_journal.py',
 'src/trading_runtime/clickhouse_transport.py',
 'research/mlops/clickhouse.py',
 'research/mlops/env.py',
 'src/trading_runtime/confirmed_original_risk_failure.py',
 'src/trading_runtime/arte_original_risk_diagnostic_v4.py',
 'src/trading_runtime/original_risk_diagnostic_profile.py',
 'src/backend/backtest_confirmed_original_risk_source.py',
 'src/trading_runtime/original_risk_checkpoint.py',
 'src/trading_runtime/original_risk_pending_snapshot.py',
 'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py',
 'src/trading_runtime/strategy_one_broker_match_snapshot.py',
 'src/trading_runtime/strategy_ninety_seven_contract.py',
 'src/trading_runtime/strategy_ninety_seven_release.py',
 'src/backend/backtest_fixed_structural_lot_configuration.py',
 'src/backend/backtest_fixed_structural_lot_projection_runtime_authority.py',
 'src/trading_runtime/declared_native_manifest.py',
 'src/backend/backtest_declared_waiting_ladder_compatibility.py')
REVIEWED_SOURCE_AST = {'pipelines/strategy_one/configuration_publisher.py': 'c0fb30741372451c93847f02a029efca753ea3850c232cead77af67dd21b33c8',
 'pipelines/strategy_one/strategy_sixty_five_configuration.py': 'aaadd3e34ede7385d3b36175af5549fec334bdae96dc9b64c17fe7b5c1c5a80e',
 'scripts/clickhouse/provision_backtest_v4_ladder_runner.py': 'ef34b7dfc73d05919f04b55d616f4950b7f6f3547a88e3a2d342ed2e6ab57f8e',
 'scripts/clickhouse/provision_backtest_v4_waiting_ladder_runner.py': 'f66d6eb0280ce8c7ac849784d8ab2641572eb24809a4865e5f670b34dc77e732',
 'scripts/clickhouse/publish_strategy_sixty_five_configuration.py': 'c4a0d33c4d04443f6d35baba948d3a0a48602078690c20fcd5c118d11fe644a5',
 'scripts/clickhouse/report_strategy_one_trades.py': 'bc8f0e8e44b6556a3a3c01d3046b9298f50db85465705dbb88d53a2fccc3d2ae',
 'src/backend/backtest_declared_initial_momentum.py': '786a12c62a157c092652c8cd7d695fe4f5e8ebfb315c9b8dbab56580fb0a4b81',
 'src/backend/backtest_declared_ladder_plan.py': 'a6db11ddef4ccfe9707fc60bf0e998c106eda12c84634b8caf49b2ff4e1373cb',
 'src/backend/backtest_declared_ladder_seed.py': '01637a936e178665f35725ea18e6d6ef132ece88a3b87a7a578d32c2010f10d8',
 'src/backend/backtest_fixed_journal_bootstrap.py': 'ded36125ae78f99442ef4eb762ce3a05245c709a45b215b3e171531e9c004e30',
 'src/backend/backtest_journal_memory.py': 'b9a2d0b1e1091f99835a018e519a7ea11ae5c169ab1d65fd7472e87f6ddf9f4e',
 'src/backend/backtest_ladder_coordinator.py': 'a74dbe92b1a4dd7faf140779136e8e0da983051f11a0714bb4ac164aafc3e8e1',
 'src/backend/backtest_ladder_entry_authority.py': '9971905edc1073434fc074dae9afdba54cc86b04793c019366c9bd9830086b21',
 'src/backend/backtest_ladder_source_authority.py': '02e9ad68a84b32da0770ba0c68d1a4fca0cc3747e78537a48da2aeb7f5d75c52',
 'src/backend/backtest_squeeze_ladder_admission.py': '7126dd2ba2046ea88c3917ed33ee4b7007a309e16aab611f86c323d407565b46',
 'src/backend/backtest_squeeze_ladder_entry.py': 'ceb72094e5eaf30e08a9334dc98ff82bec6d3ca3970438d756a038cb16781d3d',
 'src/backend/backtest_squeeze_ladder_evidence.py': '5435128884fdadb4490d876396199ad5696a65833307f8b6eae6284c23032b4e',
 'src/backend/backtest_squeeze_ladder_journal_admission.py': 'c01a7a5b87f33b2b08847bf18612eec51b69ad06d4ad692f1847450b3c8ba55b',
 'src/backend/backtest_squeeze_ladder_loader.py': 'c905275276fe6e3b85814486c5c5e58df7b8e612a9595178b4a829753713ffd3',
 'src/backend/backtest_squeeze_ladder_readback.py': 'ebe50e9a4fa2bb1f620ef4067f8cb5152f2b1802d17388c1fd70a6857b26d066',
 'src/backend/backtest_squeeze_ladder_setup.py': 'f86fab2f6a863bed811b9e1785efd17f55447f6ce84da8ad2adc9654ee1fde41',
 'src/backend/backtest_strategy_one_configuration.py': '4e65c16f50ebd070f41c88669ef6fff0be054161c5c21dfb20c6f08379e3c9de',
 'src/backend/backtest_strategy_one_coordinator.py': '7d40c9a3d75872e1ff263729eb578cf9860e913bae3863ec8889ca999e5df334',
 'src/backend/backtest_strategy_one_execution.py': '27f08b4462797b4b04d72ae28a226406352362f901a4dfb08ec95f2f154d3419',
 'src/backend/backtest_typed_projection.py': 'e4459c9091a5cc57f10f78226a8bdd71a4947cf0a7152aff205b887cd25c4a7b',
 'src/backend/backtest_typed_publisher.py': '14bf8cece8fda0438d6c4a34aa34db0ce62fea9716151becc14eec2c09233a17',
 'src/backend/backtest_v4_saved_review.py': '0383859e6f9cae8f269687f9e5f4e9775d6586e5ec63d482c70646b5adf68e3d',
 'src/backend/replay_run_service.py': '140ed6acfe829e689332b736fd26a4e6f67b13b34990fb5642c87e938bf419c6',
 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995',
 'src/data_provider/calendar.py': '82706e238be3f8e13dae1177928c02a09288fed18540932dfedc11e794ca4c0e',
 'src/trading_runtime/arte_backtest_snapshot_anchor.py': '234893befe310819b6679a5541836789dc00d23b7ad9cdc10076ca3395c01087',
 'src/trading_runtime/arte_journal_commit_v4.py': '9aa6b0453ac4c2faf47fd9e3f52085d41d5705f4d5ea89e9e8e19a3d77903fc1',
 'src/trading_runtime/arte_journal_writer.py': '98bdc5ffbd26cff46d58186f7f9019c262a990a4413dd117f4d3008035bfccd1',
 'src/trading_runtime/arte_oms_projection.py': '786291db282efced570a31474814102eef2bdb9dc74d8ddffa64cbe5ecb8d07b',
 'src/trading_runtime/arte_squeeze_ladder_schema.py': '894849755a2a22935b7d9c052dfc7b2eace9f72c73c0432f21c94880d5752c66',
 'src/trading_runtime/automatic_ladder_transport.py': 'e2b62165a417fe3465af98353c663e1417c3a7723563c45c3e63941ae722629a',
 'src/trading_runtime/entry_momentum_growth.py': '8acec288e9cf7d1c62bf8cb2fa386f82d5db69a63fc9306b90168f8cee3c0b5c',
 'src/trading_runtime/execution_policies.py': 'c0766986efe8afc2d3276e2cbb1f303a4abb99427724f2ee2c1d0a29e4e2f36c',
 'src/trading_runtime/independent_lot_protection.py': '498b88e02d483d3b8c8c327b874045df3946a80cb2cc02ac472737e1ccdd36af',
 'src/trading_runtime/numbered_fixed_strategy.py': 'a94f7b006a96f706b1f6770e4ea396cd2fd0bc21f11d69c1ba9ab816f09e11c6',
 'src/trading_runtime/order_management.py': '2c6d028018e2756b6295640f5e61fc71797d3120ef7c9062561cf258a742596e',
 'src/trading_runtime/portfolio.py': 'd14c31fd8d4c392f2f98c748b401a0b50042524a4d0b56a586b059e19056bf8f',
 'src/trading_runtime/portfolio_config.py': '370094eb372aea53b97c0a5185fc95421065d0ffc9c2a6481496e006e1c60688',
 'src/trading_runtime/runtime.py': '51db1e7f1ede6820ce8bea130bc19f4185fece58ac16b800cb0564d1be654145',
 'src/trading_runtime/session_acquisition_admission.py': '642c51e9fedd015db64aa534ddd41d92dec456fa40bb82aeb01c9965afd4bebb',
 'src/trading_runtime/squeeze_ladder_admission.py': 'b159550fded242bc676242083f882060cd4d862d749fab0e99d682d44b112a0a',
 'src/trading_runtime/squeeze_ladder_automatic.py': '0474d98c356ea6999afe132e3295fa6e703d87d5227f7fe0186d6213e9b5b91d',
 'src/trading_runtime/squeeze_ladder_columnar.py': '89ad45037b4aadf78a19420a7c6eccdbc2633d76b034070326ae980c34b90427',
 'src/trading_runtime/squeeze_ladder_cross.py': 'd47b50b0d97da17a3840a6fbf5e394a5dc1e2041a9e0c38693888eb65251f8c0',
 'src/trading_runtime/squeeze_ladder_geometry.py': '34658cac7dba475b19ca3963bbcad1397a88f5f86e4133664947c9e656671e0d',
 'src/trading_runtime/squeeze_ladder_lots.py': '0b31d6955210ec0e28f6411f2b921e465046fbda21b101da0cca12f02efc61b2',
 'src/trading_runtime/squeeze_ladder_protection.py': '31b1a3c9995f80d6dc1d314a8de26a909917bf892d9eafacc62e3ae27ac8a374',
 'src/trading_runtime/squeeze_ladder_setup.py': 'f8a63bbbb9b9312c3eb88eaaddd9e74a5fb053f9d5bdee7260fce20b9a52fe9a',
 'src/trading_runtime/strategy_orders.py': '51fab3c48cc22dd41d3438f85ab2df92a7c4e80275fc9fd985494ccbb4cd7517',
 'src/trading_runtime/strategy_registry.py': 'ef1c36159a223ce6aacdd17a7f4feed61c061c4f36a3e6bd6951f2b7db4e8e25',
 'src/trading_runtime/strategy_sixty_five_contract.py': '491691d4ffc7e750cce203e19f2359a52957e13db08958255de606f8e8ce48b1',
 'src/trading_runtime/strategy_sixty_five_release.py': '4c5015d7b3923272affe263eee8c8c2d18cf452fb7d229376203480f4078b256',
 'src/backend/backtest_market_data.py': 'ad4135f16a0979b5af821b0c00c294511635b3b84b37b90f7bdff79e503cf9d3',
 'src/backend/backtest_fixed_v4_certification.py': '9328f19a53812e9a27d4dc1f5ccfe3dab17fbada403606a4c3cc07ede89649ad',
 'src/backend/backtest_fixed_run_context.py': 'b85bfc7b517a0f0fab18062f81388516d5494fb11569e1c11e0a73760f878be0',
 'src/backend/backtest_fixed_v3_preflight.py': 'a2c8e76fd5518de9abce460427a2cf860f6db5872595336a34302f50c00b2452',
 'src/backend/backtest_v4_keeper_lease.py': '7f3a0f228d1df853b9b2bf58d640eccc37b80aa7efda17f4e878b5d2614e4637',
 'src/backend/backtest_saved_source_authority.py': 'b0f17ce10cdfdbaf09315e64899dff9c6ef87b0fd520bfa334649401bcafd6bf',
 'src/backend/typed_backtest_review_core.py': '08f30c8838b3a3bc59fab346cf491ec8a20803a066f42184a007ac5be0d81f16',
 'src/trading_runtime/arte_backtest_definition.py': '70a3b6031bff897ccf9ae6e33e09c449898bea558f1dce05e512ac0875d7df5f',
 'src/trading_runtime/arte_journal_schema.py': '3d194bbbac0dc8628ac296925fcf3ea42ff9fef1140f516584b72fbc9e0d6a5c',
 'src/trading_runtime/arte_journal_reader.py': 'c99c2bae8b6647c5ea1f4185ea7dab38a807b821b040eb90e1a12b9949860ae2',
 'src/trading_runtime/arte_journal_projection.py': '9c78a31a6bad5fb1026053f0b5552c9ed37bf717c75143da25f14d9df3e08efc',
 'src/trading_runtime/arte_typed_insert_dispatch.py': '429b4a0d32b035f264e8dd67324f81ae54f7ccd243055eeee6c0b34806662d43',
 'src/trading_runtime/journal_contract.py': '130e8b70f3708cd30f0524b035a0c99cded407bf6b1fc39a610cb7e19458da28',
 'src/trading_runtime/strategy_one_configuration_tree.py': '44f46197d4075bbdae4d39c7f4b8160fc6ae4895e11a90f13db927f4268b7e06',
 'src/trading_runtime/strategy_engine.py': '48f5a2379095297984334ac046dabd8e36c8c3288434852f8454aed52cc8eaa6',
 'src/trading_runtime/keeper_session.py': '316240cc8dde1b4047d618efc89fc8e5bd281770e4db5d9c1267f2147fb06a18',
 'src/trading_runtime/keeper_ownership.py': '4d50a7acea06377df65ab79c506685c8ce426d1981e867049341c93e3047c2f7',
 'scripts/clickhouse/provision_backtest_v4_runner.py': 'a97b2323c1104b0c81396cc9ad5999d5962ce44bffc8617916ff9f67993fe056',
 'scripts/clickhouse/provision_fixed_backtest_v3_principals.py': '77df1f30c5fb6fb5700f2b8843a2d71a0f801beb27026371678666e5d228a6ee',
 'scripts/clickhouse/provision_trading_journal.py': 'fb0a05f5a3098bcd28482db4b7f34dc3a47e3ac436bca0b0ca747262dd1e507e',
 'src/trading_runtime/clickhouse_transport.py': '108e4e9a4e757ed792ab01f8d7faefdc035b2da398cfcbdd8dc3917c20cbb5df',
 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
 'research/mlops/env.py': '856dc976500cfd9efc0648b22a28665951f8bdeb81328c45a47f5dc586b5895b',
 'src/trading_runtime/confirmed_original_risk_failure.py': '738493dce849c08fd7e1d4f5c24a7c3188bc772a183d9083a86713177aba6f39',
 'src/trading_runtime/arte_original_risk_diagnostic_v4.py': '747bd14c7cd27adc1151770c7b946d6c14074a2e813f167dcf7e4241604176d2',
 'src/trading_runtime/original_risk_diagnostic_profile.py': 'fddb82aaa11dffd1d56017b0ae6bb2ca382532c52eeb4dc1ef75095dba979baf',
 'src/backend/backtest_confirmed_original_risk_source.py': 'f51caf0f2197a606d02de3972aaeb41b9291dd487a2549fe2fd9987162bfa859',
 'src/trading_runtime/original_risk_checkpoint.py': 'ab857abd97f792f7cb9c9ebb7187d86df5747ce554cc1f5f3b8157ca4801e38b',
 'src/trading_runtime/original_risk_pending_snapshot.py': '0c18cf677683921ff30f41c6b5dde78a43e5ad190e34bdde572baeabb14d9cac',
 'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'dfcec9444db48a1b0e4ccc6a1bfb8706353832e237f35ff4463821335a458a4c',
 'src/trading_runtime/strategy_one_broker_match_snapshot.py': '4dff4622888b967be79d4c1a1ece64e124153e56a812b37bea3a3c72a8469672',
 'src/trading_runtime/strategy_ninety_seven_contract.py': '1123bbca56fa23c7488f818eedd199573c2d092b5fb4d7a238df55171b330cab',
 'src/trading_runtime/strategy_ninety_seven_release.py': '18a3e3b53b85ab70f7c8ad0991b7b43a4d9872c2b5dbaf71e13b41fab64119be',
 'src/backend/backtest_fixed_structural_lot_configuration.py': 'b4e996f671d4a5071da91afceabd520b74bae654015933a745479a3dea2ed821',
 'src/backend/backtest_fixed_structural_lot_projection_runtime_authority.py': '1699f6149dc487e0e751bbace4a666a528bae680d4e87d1f2508a6ba00a0b178',
 'src/trading_runtime/declared_native_manifest.py': 'c47104ec813acd9f299b4152176ba65d339892c4dcb5c1b6a2f4a9be4838c39b',
 'src/backend/backtest_declared_waiting_ladder_compatibility.py': '76c1110f896c3aeda9b558df0640cb3ae7a616699c87e50144b2c7a5868905a1'}
SOURCE_REVIEW_ORIGINS = {'pipelines/strategy_one/configuration_publisher.py': 'reviewed-declared-native-manifest-route-v1',
 'pipelines/strategy_one/strategy_sixty_five_configuration.py': 'waiting-baseline-reviewed-module',
 'scripts/clickhouse/provision_backtest_v4_ladder_runner.py': 'waiting-baseline-reviewed-module',
 'scripts/clickhouse/provision_backtest_v4_waiting_ladder_runner.py': 'waiting-baseline-reviewed-module',
 'scripts/clickhouse/publish_strategy_sixty_five_configuration.py': 'waiting-baseline-reviewed-module',
 'scripts/clickhouse/report_strategy_one_trades.py': 'selected-exit-reviewed-module',
 'src/backend/backtest_declared_initial_momentum.py': 'waiting-baseline-reviewed-module',
 'src/backend/backtest_declared_ladder_plan.py': 'waiting-baseline-reviewed-module',
 'src/backend/backtest_declared_ladder_seed.py': 'waiting-baseline-reviewed-module',
 'src/backend/backtest_fixed_journal_bootstrap.py': 'selected-exit-reviewed-module',
 'src/backend/backtest_journal_memory.py': 'selected-exit-reviewed-module',
 'src/backend/backtest_ladder_coordinator.py': 'waiting-baseline-reviewed-module',
 'src/backend/backtest_ladder_entry_authority.py': 'waiting-baseline-reviewed-module',
 'src/backend/backtest_ladder_source_authority.py': 'waiting-baseline-reviewed-module',
 'src/backend/backtest_squeeze_ladder_admission.py': 'waiting-baseline-reviewed-module',
 'src/backend/backtest_squeeze_ladder_entry.py': 'waiting-baseline-reviewed-module',
 'src/backend/backtest_squeeze_ladder_evidence.py': 'waiting-baseline-reviewed-module',
 'src/backend/backtest_squeeze_ladder_journal_admission.py': 'waiting-baseline-reviewed-module',
 'src/backend/backtest_squeeze_ladder_loader.py': 'waiting-baseline-reviewed-module',
 'src/backend/backtest_squeeze_ladder_readback.py': 'waiting-baseline-reviewed-module',
 'src/backend/backtest_squeeze_ladder_setup.py': 'waiting-baseline-reviewed-module',
 'src/backend/backtest_strategy_one_configuration.py': 'reviewed-declared-native-manifest-route-v1',
 'src/backend/backtest_strategy_one_coordinator.py': 'waiting-baseline-reviewed-module',
 'src/backend/backtest_strategy_one_execution.py': 'selected-exit-reviewed-module',
 'src/backend/backtest_typed_projection.py': 'selected-exit-reviewed-module',
 'src/backend/backtest_typed_publisher.py': 'selected-exit-reviewed-module',
 'src/backend/backtest_v4_saved_review.py': 'selected-exit-reviewed-module',
 'src/backend/replay_run_service.py': 'selected-exit-reviewed-module',
 'src/backend/source_ast_summary.py': 'waiting-baseline-reviewed-module',
 'src/data_provider/calendar.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/arte_backtest_snapshot_anchor.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/arte_journal_commit_v4.py': 'selected-exit-reviewed-module',
 'src/trading_runtime/arte_journal_writer.py': 'selected-exit-reviewed-module',
 'src/trading_runtime/arte_oms_projection.py': 'selected-exit-reviewed-module',
 'src/trading_runtime/arte_squeeze_ladder_schema.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/automatic_ladder_transport.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/entry_momentum_growth.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/execution_policies.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/independent_lot_protection.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/numbered_fixed_strategy.py': 'reviewed-declared-native-manifest-route-v1',
 'src/trading_runtime/order_management.py': 'selected-exit-reviewed-module',
 'src/trading_runtime/portfolio.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/portfolio_config.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/runtime.py': 'selected-exit-reviewed-module',
 'src/trading_runtime/session_acquisition_admission.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/squeeze_ladder_admission.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/squeeze_ladder_automatic.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/squeeze_ladder_columnar.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/squeeze_ladder_cross.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/squeeze_ladder_geometry.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/squeeze_ladder_lots.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/squeeze_ladder_protection.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/squeeze_ladder_setup.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/strategy_orders.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/strategy_registry.py': 'reviewed-declared-native-manifest-route-v1',
 'src/trading_runtime/strategy_sixty_five_contract.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/strategy_sixty_five_release.py': 'waiting-baseline-reviewed-module',
 'src/backend/backtest_market_data.py': 'waiting-baseline-reviewed-module',
 'src/backend/backtest_fixed_v4_certification.py': 'reviewed-declared-native-manifest-whole-dispatcher-and-compatibility-v1',
 'src/backend/backtest_fixed_run_context.py': 'waiting-baseline-reviewed-module',
 'src/backend/backtest_fixed_v3_preflight.py': 'waiting-baseline-reviewed-module',
 'src/backend/backtest_v4_keeper_lease.py': 'waiting-baseline-reviewed-module',
 'src/backend/backtest_saved_source_authority.py': 'selected-exit-reviewed-module',
 'src/backend/typed_backtest_review_core.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/arte_backtest_definition.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/arte_journal_schema.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/arte_journal_reader.py': 'selected-exit-reviewed-module',
 'src/trading_runtime/arte_journal_projection.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/arte_typed_insert_dispatch.py': 'selected-exit-reviewed-module',
 'src/trading_runtime/journal_contract.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/strategy_one_configuration_tree.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/strategy_engine.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/keeper_session.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/keeper_ownership.py': 'waiting-baseline-reviewed-module',
 'scripts/clickhouse/provision_backtest_v4_runner.py': 'waiting-baseline-reviewed-module',
 'scripts/clickhouse/provision_fixed_backtest_v3_principals.py': 'waiting-baseline-reviewed-module',
 'scripts/clickhouse/provision_trading_journal.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/clickhouse_transport.py': 'waiting-baseline-reviewed-module',
 'research/mlops/clickhouse.py': 'waiting-baseline-reviewed-module',
 'research/mlops/env.py': 'waiting-baseline-reviewed-module',
 'src/trading_runtime/confirmed_original_risk_failure.py': 'selected-exit-reviewed-module',
 'src/trading_runtime/arte_original_risk_diagnostic_v4.py': 'selected-exit-reviewed-module',
 'src/trading_runtime/original_risk_diagnostic_profile.py': 'selected-exit-reviewed-module',
 'src/backend/backtest_confirmed_original_risk_source.py': 'selected-exit-reviewed-module',
 'src/trading_runtime/original_risk_checkpoint.py': 'selected-exit-reviewed-module',
 'src/trading_runtime/original_risk_pending_snapshot.py': 'selected-exit-reviewed-module',
 'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'selected-exit-reviewed-module',
 'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'selected-exit-reviewed-module',
 'src/trading_runtime/strategy_ninety_seven_contract.py': 'prepared-waiting-swing-contract-review',
 'src/trading_runtime/strategy_ninety_seven_release.py': 'prepared-waiting-swing-contract-review',
 'src/backend/backtest_fixed_structural_lot_configuration.py': 'selected-exit-reviewed-module',
 'src/backend/backtest_fixed_structural_lot_projection_runtime_authority.py': 'selected-exit-reviewed-module',
 'src/trading_runtime/declared_native_manifest.py': 'reviewed-declared-native-manifest-route-v1',
 'src/backend/backtest_declared_waiting_ladder_compatibility.py': 'reviewed-declared-native-manifest-route-v1'}
PENDING_SOURCE_REVIEWS = ()
APPROVED_METADATA_ANCHOR = 'b5b98e99d25d1238107e0decdc650631b36ad0dc3c6b77554be689460d313cf3'
APPROVED_SELF_AST = '505ca8b96cb7957e04a5e87f41e1718c3596d0ad0712bc77cfaac9f577c95377'

def certify_declared_waiting_ladder_source() -> str:
    """Validate reviewed source first; issue no authority while reviews remain."""
    root = Path(__file__).resolve().parents[2]
    own = root / 'src/backend/backtest_declared_waiting_ladder_certification.py'
    try:
        own_source = own.read_text(encoding='utf-8')
        tree = ast.parse(own_source)
    except (OSError, SyntaxError) as exc:
        raise ValueError('Waiting-ladder certifier cannot be read') from exc
    names = ('REQUIRED_SOURCE_FILES', 'REVIEWED_SOURCE_AST', 'SOURCE_REVIEW_ORIGINS',
             'PENDING_SOURCE_REVIEWS', 'APPROVED_METADATA_ANCHOR', 'APPROVED_SELF_AST')
    fresh = {}
    for name in names:
        nodes = [n for n in tree.body if type(n) is ast.Assign and len(n.targets) == 1
                 and type(n.targets[0]) is ast.Name and n.targets[0].id == name]
        if len(nodes) != 1:
            raise ValueError('Waiting-ladder certifier declaration shape differs')
        try:
            fresh[name] = ast.literal_eval(nodes[0].value)
        except (ValueError, TypeError) as exc:
            raise ValueError('Waiting-ladder declarations must be literal') from exc
    loaded = dict(zip(names, (REQUIRED_SOURCE_FILES, REVIEWED_SOURCE_AST,
        SOURCE_REVIEW_ORIGINS, PENDING_SOURCE_REVIEWS,
        APPROVED_METADATA_ANCHOR, APPROVED_SELF_AST)))
    if fresh != loaded:
        raise ValueError('Waiting-ladder loaded and fresh declarations differ')
    # Seal the complete envelope, including imports, function, and declaration
    # names; normalize only literal metadata values to break self-reference.
    for node in tree.body:
        if type(node) is ast.Assign and len(node.targets) == 1 and type(node.targets[0]) is ast.Name:
            if node.targets[0].id in names:
                node.value = ast.Constant(None)
    if sha256(ast.unparse(tree).encode()).hexdigest() != APPROVED_SELF_AST:
        raise ValueError('Waiting-ladder certifier envelope source differs')
    required, pending = REQUIRED_SOURCE_FILES, PENDING_SOURCE_REVIEWS
    if (type(required) is not tuple or not required or len(set(required)) != len(required)
            or any(type(p) is not str or not p.startswith(('src/', 'pipelines/', 'scripts/', 'research/'))
                or '..' in p.split('/') or '\\' in p or not p.endswith('.py') for p in required)
            or type(pending) is not tuple or len(set(pending)) != len(pending)
            or any(p not in required for p in pending)
            or type(REVIEWED_SOURCE_AST) is not dict or type(SOURCE_REVIEW_ORIGINS) is not dict
            or tuple(REVIEWED_SOURCE_AST) != tuple(p for p in required if p not in pending)
            or tuple(SOURCE_REVIEW_ORIGINS) != tuple(REVIEWED_SOURCE_AST)):
        raise ValueError('Waiting-ladder complete review inventory differs')
    metadata = dict(required=required, sources=REVIEWED_SOURCE_AST,
                    review_origins=SOURCE_REVIEW_ORIGINS, pending=pending)
    if sha256(json.dumps(metadata, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != APPROVED_METADATA_ANCHOR:
        raise ValueError('Waiting-ladder source metadata anchor differs')
    observed = [('certifier_envelope', APPROVED_SELF_AST), ('certifier_metadata', APPROVED_METADATA_ANCHOR)]
    sealed = []
    for relative in required:
        path = root / relative
        if not path.is_file() or path.is_symlink() or root not in path.resolve().parents:
            raise ValueError('Waiting-ladder source path is missing or foreign: ' + relative)
        try:
            source = path.read_text(encoding='utf-8')
            selected = ast.parse(source)
        except (OSError, SyntaxError) as exc:
            raise ValueError('Waiting-ladder source cannot be parsed: ' + relative) from exc
        if relative not in pending:
            expected = REVIEWED_SOURCE_AST[relative]
            if type(expected) is dict:
                if not expected:
                    raise ValueError('Waiting-ladder symbol review is empty: ' + relative)
                for symbol, digest in expected.items():
                    nodes = [n for n in selected.body if type(n) is ast.FunctionDef and n.name == symbol]
                    if len(nodes) != 1 or sha256(ast.unparse(nodes[0]).encode()).hexdigest() != digest:
                        raise ValueError('Waiting-ladder reviewed symbol changed: ' + relative + ':' + symbol)
            elif type(expected) is not str or sha256(ast.unparse(selected).encode()).hexdigest() != expected:
                raise ValueError('Waiting-ladder reviewed source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
        sealed.append((path, source))
    if any(path.read_text(encoding='utf-8') != source for path, source in sealed):
        raise ValueError('Waiting-ladder source changed during certification')
    if own.read_text(encoding='utf-8') != own_source:
        raise ValueError('Waiting-ladder certifier changed during certification')
    if pending:
        raise ValueError('Waiting-ladder source reviews remain unapproved: ' + ', '.join(pending))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
