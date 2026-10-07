"""Closed selected consecutive original-risk failure source authority; publication is separate."""
from hashlib import sha256
import json
from pathlib import Path
import ast

REQUIRED_SOURCE_FILES = (
    'pipelines/strategy_one/configuration_publisher.py',
    'pipelines/strategy_one/strategy_sixty_nine_configuration.py',
    'scripts/clickhouse/install_trading_journal_layout.py',
    'scripts/clickhouse/plan_trading_journal_layout.py',
    'scripts/clickhouse/provision_backtest_v4_runner.py',
    'scripts/clickhouse/publish_strategy_sixty_nine_configuration.py',
    'scripts/clickhouse/report_strategy_one_trades.py',
    'src/backend/backtest_declared_entry_quote_source.py',
    'src/backend/backtest_declared_initial_momentum.py',
    'src/backend/backtest_entry_spread_risk.py',
    'src/backend/backtest_entry_spread_risk_v2.py',
    'src/backend/backtest_fixed_journal_bootstrap.py',
    'src/backend/backtest_fixed_v4_certification.py',
    'src/backend/backtest_journal_memory.py',
    'src/backend/backtest_strategy_certified_price_break.py',
    'src/backend/backtest_strategy_episode_activity_source.py',
    'src/backend/backtest_strategy_liquidity_fade.py',
    'src/backend/backtest_strategy_liquidity_fade_loader.py',
    'src/backend/backtest_strategy_one_configuration.py',
    'src/backend/backtest_strategy_one_coordinator.py',
    'src/backend/backtest_strategy_one_execution.py',
    'src/backend/backtest_strategy_one_management.py',
    'src/backend/backtest_typed_projection.py',
    'src/backend/backtest_typed_publisher.py',
    'src/backend/backtest_v4_saved_review.py',
    'src/backend/replay_run_service.py',
    'src/backend/source_ast_summary.py',
    'src/trading_runtime/arte_confirmed_ah_failure_v4.py',
    'src/trading_runtime/arte_entry_activity_v4.py',
    'src/trading_runtime/arte_entry_spread_risk_v4.py',
    'src/trading_runtime/arte_first_price_entry_v4.py',
    'src/trading_runtime/arte_followthrough_failure_v4.py',
    'src/trading_runtime/arte_initial_momentum_entry_v4.py',
    'src/trading_runtime/arte_journal_commit_v4.py',
    'src/trading_runtime/arte_journal_compound_v4.py',
    'src/trading_runtime/arte_journal_writer.py',
    'src/trading_runtime/arte_liquidity_fade_failure_v4.py',
    'src/trading_runtime/arte_oms_projection.py',
    'src/trading_runtime/arte_profit_giveback_v4.py',
    'src/trading_runtime/arte_rising_momentum_entry_v4.py',
    'src/trading_runtime/arte_strategy_one_entry_journal.py',
    'src/trading_runtime/declared_followthrough_failure.py',
    'src/trading_runtime/declared_profit_giveback.py',
    'src/trading_runtime/early_original_risk_failure.py',
    'src/trading_runtime/entry_momentum_growth.py',
    'src/trading_runtime/entry_spread_risk.py',
    'src/trading_runtime/numbered_fixed_strategy.py',
    'src/trading_runtime/runtime.py',
    'src/trading_runtime/strategy_confirmed_ah_failure_exit.py',
    'src/trading_runtime/strategy_confirmed_ah_failure_source.py',
    'src/trading_runtime/strategy_forty_two_release.py',
    'src/trading_runtime/strategy_sixty_nine_contract.py',
    'src/trading_runtime/strategy_sixty_nine_release.py',
    'src/trading_runtime/strategy_followthrough_exit.py',
    'src/trading_runtime/strategy_half_risk_liquidity_fade.py',
    'src/trading_runtime/strategy_liquidity_fade_exit.py',
    'src/trading_runtime/strategy_liquidity_fade_publication.py',
    'src/trading_runtime/strategy_liquidity_fade_source.py',
    'src/trading_runtime/strategy_one_management_snapshot.py',
    'src/trading_runtime/strategy_profit_giveback.py',
    'src/trading_runtime/strategy_profit_giveback_arm.py',
    'src/trading_runtime/strategy_profit_giveback_exit.py',
    'src/trading_runtime/strategy_profit_giveback_source.py',
    'src/trading_runtime/strategy_registry.py',
    'src/trading_runtime/strategy_rising_momentum_witness.py',
    'src/trading_runtime/squeeze_ladder_geometry.py',
    'src/trading_runtime/all_held_original_risk_failure.py',
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
    'scripts/clickhouse/provision_fixed_backtest_v3_principals.py',
    'scripts/clickhouse/provision_trading_journal.py',
    'src/backend/backtest_strategy_sixty_nine_certification.py',
    'src/trading_runtime/clickhouse_transport.py',
    'research/mlops/clickhouse.py',
    'research/mlops/env.py',
    'src/trading_runtime/confirmed_original_risk_failure.py',
    'src/backend/backtest_confirmed_original_risk_source.py',
    'src/trading_runtime/arte_original_risk_diagnostic_v4.py',
    'src/trading_runtime/original_risk_diagnostic_profile.py',
    'src/backend/backtest_v4_performance_evidence.py',
    'scripts/clickhouse/provision_backtest_v4_original_risk_runner.py',
    'src/backend/backtest_market_data.py',
    'src/backend/backtest_declared_ladder_plan.py',
    'src/backend/backtest_ladder_source_authority.py',
    'src/trading_runtime/squeeze_ladder_automatic.py',
    'src/trading_runtime/original_risk_checkpoint.py',
    'src/trading_runtime/original_risk_pending_snapshot.py',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py',
    'src/trading_runtime/strategy_sixty_eight_release.py',
    'src/trading_runtime/strategy_sixty_eight_contract.py',
)
STRATEGY69_SOURCE_AST = {
    'pipelines/strategy_one/configuration_publisher.py': '945f397905e04eed4c98c4b673161e4c2549a961540b085f4c90b01a1817b597',
    'pipelines/strategy_one/strategy_sixty_nine_configuration.py': '98495446cc5f924100dd92a052a654131f4db11c63376b083364c7488b2f601b',
    'scripts/clickhouse/install_trading_journal_layout.py': 'b7dad511f062c717beee0ff60fbc6b4eba19cc9fab34d1735c07ee279dda00b8',
    'scripts/clickhouse/plan_trading_journal_layout.py': '0c811495a1a760a1a0ecc254ecd1705a12c94110dc0e5c33b992704fc7765f6d',
    'scripts/clickhouse/provision_backtest_v4_runner.py': 'a97b2323c1104b0c81396cc9ad5999d5962ce44bffc8617916ff9f67993fe056',
    'scripts/clickhouse/publish_strategy_sixty_nine_configuration.py': 'b6082fbbdd0336d76b70d281ce73f502d37bfda19e3cf3ccd20883fe9b1092b6',
    'scripts/clickhouse/report_strategy_one_trades.py': 'ae5e51880141272adf310070c163d59c287ee90433e87ec89490414f0f581f3f',
    'src/backend/backtest_declared_entry_quote_source.py': '7a812908fd044f2573d525ac54b59383ab241ca673dbe8194ef30b12c79259e7',
    'src/backend/backtest_declared_initial_momentum.py': '786a12c62a157c092652c8cd7d695fe4f5e8ebfb315c9b8dbab56580fb0a4b81',
    'src/backend/backtest_entry_spread_risk.py': 'e1ca99c0382dd355000b122742acbc3fd3ff0a703f5204172eee76584cc44a1d',
    'src/backend/backtest_entry_spread_risk_v2.py': '0287cbbb1d9795121b23f1fa666e128135001cb1129c9b5b16cc0ac951e13cb9',
    'src/backend/backtest_fixed_journal_bootstrap.py': 'b1e67803f6ddc4b3c28e4030febd61cc8800d0f6d3e21f151b88fd24f4ce689d',
    'src/backend/backtest_fixed_v4_certification.py': {'certify_numbered_fixed_v4_projection': '2bfaf370cdc6959eb85e85607d1a3e8ed28445f8c0ccf9d99a6c4fae28fde4d2'},
    'src/backend/backtest_journal_memory.py': '7967a2dc2bd11edd2739caa04a8c27c8fb80f3d900da8bb642a5509deaf08f33',
    'src/backend/backtest_strategy_certified_price_break.py': 'e1fa545fbbd95b4784e4cf2e0ef320276a4ff525683f680ddea6fc0ccd4c1a38',
    'src/backend/backtest_strategy_episode_activity_source.py': '2d4fa40c46a866be83328ce7443a350655620bf9c4b4a82044dc45ee7441c8c0',
    'src/backend/backtest_strategy_liquidity_fade.py': '4870509aaef5af9a6b45d7a9c389dfe737a927f0beb71164b00ef9919d1a7e24',
    'src/backend/backtest_strategy_liquidity_fade_loader.py': 'd6e5660d40bb75df739f8fe862ac7a8ed8441cdd7c58da64184f219d5d8a62b4',
    'src/backend/backtest_strategy_one_configuration.py': '0cb7e0a7b553f2be2327f7646b06f8f38861743939a881ab877443c9c3bc1b0a',
    'src/backend/backtest_strategy_one_coordinator.py': '7d40c9a3d75872e1ff263729eb578cf9860e913bae3863ec8889ca999e5df334',
    'src/backend/backtest_strategy_one_execution.py': 'd9ed2de2119170cf4650b139e16004c170fd2503a482831b85f40a1177ab6f8d',
    'src/backend/backtest_strategy_one_management.py': '7cbb9ba74aef32d427659a17ed736f64d72f7dcdd80d4e24cb6ec7634c22e5c0',
    'src/backend/backtest_typed_projection.py': '30e7ad90b8d41270aa80ced61ed1e9b3fdd539265ff7395b9285308237db3a9e',
    'src/backend/backtest_typed_publisher.py': '0b496a1667ee617a96238773ec2cbc306b4a4f798db2fc4ea0280f7d7767661f',
    'src/backend/backtest_v4_saved_review.py': 'f6a2048ea195b7a39e6694e89cb7f164e4d83826197727ee90dceefb5e852ce9',
    'src/backend/replay_run_service.py': '341053ab5a8a8c0d51dfafa62893237801379896aa690b2185900d6f66dec8c1',
    'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995',
    'src/trading_runtime/arte_confirmed_ah_failure_v4.py': '5f8b90176e9bbe6232a1f7b88a86333cc2466dc5a932740d27b7cbf4d4345435',
    'src/trading_runtime/arte_entry_activity_v4.py': '95b32e75b49992cb38e0569e1576e093841f46eb3b8defd37e5ec4d0e8494b92',
    'src/trading_runtime/arte_entry_spread_risk_v4.py': '936b3a9b67c7d9ad9be63a0b44b6d9255e0275941f9c4ef95eaf2779b1c6f3e6',
    'src/trading_runtime/arte_first_price_entry_v4.py': 'c968e50cb42d4326f9499b20134ec6500990058a6415aed581f373fb2d2efec6',
    'src/trading_runtime/arte_followthrough_failure_v4.py': '2ce1e6805a9db71fa5afe0ec63aba61a7d87fc95e3847b89bd7e94b93aa1bc6c',
    'src/trading_runtime/arte_initial_momentum_entry_v4.py': '037069b752fbc7af9de87ba44f53d3af79833cdb379872ce2554013456af21ea',
    'src/trading_runtime/arte_journal_commit_v4.py': 'fd634b0f4f16f673f6795479fd03ef258c81b33b6e264f5395305b936c4b0a1f',
    'src/trading_runtime/arte_journal_compound_v4.py': '05b28c131df86e9b743117c1c801eec7384995b0bbe8c8b80c9afb53e183eb4c',
    'src/trading_runtime/arte_journal_writer.py': 'faafb31844ea3c0dfd7c7d5f205552d01c3e6652179612ef57298a918f93d37c',
    'src/trading_runtime/arte_liquidity_fade_failure_v4.py': '216e413631d81e7cb0b29d7103ab1939320946a43a6efeba17c646cdc6147134',
    'src/trading_runtime/arte_oms_projection.py': 'e0accf89d43ab445f0d0520d4b4b811c86381b540043fc1be9ee982dd419e25f',
    'src/trading_runtime/arte_profit_giveback_v4.py': '3e22f8f0c57d85a471cd15a63e18295517c617f9b8dc88eae5d1cc7908a70293',
    'src/trading_runtime/arte_rising_momentum_entry_v4.py': '2f9b2689448c0592d99134286955618d0c312c29d8712d37f79310bffbb55d1d',
    'src/trading_runtime/arte_strategy_one_entry_journal.py': '45dc2b6abf7f528fbf1e056c5249a78eb1b52813f051a732512712abc5993856',
    'src/trading_runtime/declared_followthrough_failure.py': 'ad60271c51250ee004e00e5fc00f8bd8e220794968a9421ec0ff73bd002d5c98',
    'src/trading_runtime/declared_profit_giveback.py': '944de8d53f75e5f270120fa36d5d8786a6ea167ed824f1d8e501c8a9ae120bc3',
    'src/trading_runtime/early_original_risk_failure.py': '1df39278ef45fd7392fa570702b836588c21274aa0eb36c3667477dbcef0dc89',
    'src/trading_runtime/entry_momentum_growth.py': '8acec288e9cf7d1c62bf8cb2fa386f82d5db69a63fc9306b90168f8cee3c0b5c',
    'src/trading_runtime/entry_spread_risk.py': 'cbb5fcbae0b6c6581a3dd9a2d59dd6dbc57372799f2781e3d69c4559b5a6be8e',
    'src/trading_runtime/numbered_fixed_strategy.py': '96d118e8a2ad954cb3e79b742df4634d07bae2b81913036ae78737285bd7954d',
    'src/trading_runtime/runtime.py': '6e8c043db1d62b04560fc581e46f3f7d0382c49285975263dc6cff4b40dd8dc8',
    'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '5fce50ffc270e19c98f200dbe949edd9ce718959d8f7cb48324dce5f1e96590f',
    'src/trading_runtime/strategy_confirmed_ah_failure_source.py': '5b5fbaeda54d8e7593d6646c7d6631ab8323ee472cdf73ecee947e049a77b50a',
    'src/trading_runtime/strategy_forty_two_release.py': 'ae2e532ddaf0adc3b40ad77a40f8d3fd9cbd6154c344671a17e66545cc81656f',
    'src/trading_runtime/strategy_sixty_nine_contract.py': 'e3b165ed2965187f57828f2170829140d7ea976636b2bc9bc39574cc1223c71f',
    'src/trading_runtime/strategy_sixty_nine_release.py': 'fbf61d432b2263092da9766fc004e0c455078e05032ccd20f7d683d9eba1e292',
    'src/trading_runtime/strategy_followthrough_exit.py': '85057326d73128bfe04f23fdd72f433693aa49b5837a06354bdba1e5a76b628b',
    'src/trading_runtime/strategy_half_risk_liquidity_fade.py': '3f8d8949125090eda81d3256cae65920f950da12cbbb88dc6fb30a922c4cbe21',
    'src/trading_runtime/strategy_liquidity_fade_exit.py': '3ccbc3b5f09d6553a23372e9a2c435c936defa2dc800717d36f2824c21a04325',
    'src/trading_runtime/strategy_liquidity_fade_publication.py': '3ae4e11e8b2d2d9f470c4beab7c0af09b0259c1168a367f728778ed8accb6827',
    'src/trading_runtime/strategy_liquidity_fade_source.py': '5554b994c4ac8c566a1883c676c3c01206ee9d091ba0e10dc2c7c81890f126b2',
    'src/trading_runtime/strategy_one_management_snapshot.py': '102cb6778c4cde6bd89393cd7338962b0092a9ad9414b57b17976081299f90b3',
    'src/trading_runtime/strategy_profit_giveback.py': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10',
    'src/trading_runtime/strategy_profit_giveback_arm.py': 'ef5b2892793ecf7ef2d6957a0851c1c2010e1d4da5c21c7e0dbc5de46fc39f41',
    'src/trading_runtime/strategy_profit_giveback_exit.py': 'c8466273babe33b6c54ed898d446a6b1b68297fd2e37e69b27270a4bc804b49e',
    'src/trading_runtime/strategy_profit_giveback_source.py': 'b3e58e1bf6cbb1f77a79ddfd41a8ce7115c634dc80753877549f37947db75189',
    'src/trading_runtime/strategy_registry.py': '77c02adbc84466c2f827441482d438cabfce021c5430828d2805cd4438bb4d4b',
    'src/trading_runtime/strategy_rising_momentum_witness.py': '8433d7be44361854221822708c23dbbc7899b106a16aa5d9f62d2e1825d6bf30',
    'src/trading_runtime/squeeze_ladder_geometry.py': '34658cac7dba475b19ca3963bbcad1397a88f5f86e4133664947c9e656671e0d',
    'src/trading_runtime/all_held_original_risk_failure.py': '093bb7f1b73277f21aa1a399a7b3b9f36d77dea8ca614f1130314029fcfaf8cf',
    'src/backend/backtest_fixed_run_context.py': 'b85bfc7b517a0f0fab18062f81388516d5494fb11569e1c11e0a73760f878be0',
    'src/backend/backtest_fixed_v3_preflight.py': 'a2c8e76fd5518de9abce460427a2cf860f6db5872595336a34302f50c00b2452',
    'src/backend/backtest_v4_keeper_lease.py': '7f3a0f228d1df853b9b2bf58d640eccc37b80aa7efda17f4e878b5d2614e4637',
    'src/backend/backtest_saved_source_authority.py': 'bb486b3cd6337d13ab5bfa9aea9e6614ab8e5a5a3bac558b06194cc240d8a5fc',
    'src/backend/typed_backtest_review_core.py': '08f30c8838b3a3bc59fab346cf491ec8a20803a066f42184a007ac5be0d81f16',
    'src/trading_runtime/arte_backtest_definition.py': '70a3b6031bff897ccf9ae6e33e09c449898bea558f1dce05e512ac0875d7df5f',
    'src/trading_runtime/arte_journal_schema.py': '3d194bbbac0dc8628ac296925fcf3ea42ff9fef1140f516584b72fbc9e0d6a5c',
    'src/trading_runtime/arte_journal_reader.py': '71e11a8f9a853e9d2efa9153cdcb36a931726aec7412da5e9c241fd1713d2973',
    'src/trading_runtime/arte_journal_projection.py': '9c78a31a6bad5fb1026053f0b5552c9ed37bf717c75143da25f14d9df3e08efc',
    'src/trading_runtime/arte_typed_insert_dispatch.py': '5d08bb4fca7c8af5ae6ef711f16f99fe38cca561c46207142bb06640cb810d93',
    'src/trading_runtime/journal_contract.py': '130e8b70f3708cd30f0524b035a0c99cded407bf6b1fc39a610cb7e19458da28',
    'src/trading_runtime/strategy_one_configuration_tree.py': '44f46197d4075bbdae4d39c7f4b8160fc6ae4895e11a90f13db927f4268b7e06',
    'src/trading_runtime/strategy_engine.py': '48f5a2379095297984334ac046dabd8e36c8c3288434852f8454aed52cc8eaa6',
    'src/trading_runtime/keeper_session.py': '316240cc8dde1b4047d618efc89fc8e5bd281770e4db5d9c1267f2147fb06a18',
    'src/trading_runtime/keeper_ownership.py': '4d50a7acea06377df65ab79c506685c8ce426d1981e867049341c93e3047c2f7',
    'scripts/clickhouse/provision_fixed_backtest_v3_principals.py': '77df1f30c5fb6fb5700f2b8843a2d71a0f801beb27026371678666e5d228a6ee',
    'scripts/clickhouse/provision_trading_journal.py': 'fb0a05f5a3098bcd28482db4b7f34dc3a47e3ac436bca0b0ca747262dd1e507e',
    'src/backend/backtest_strategy_sixty_nine_certification.py': {'certify_strategy_sixty_nine_source': '0e79c8c5f20c62ffeaa16fab640f4359d6623971217ea6a679f73f63e6dd9f52'},
    'src/trading_runtime/clickhouse_transport.py': '108e4e9a4e757ed792ab01f8d7faefdc035b2da398cfcbdd8dc3917c20cbb5df',
    'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
    'research/mlops/env.py': '856dc976500cfd9efc0648b22a28665951f8bdeb81328c45a47f5dc586b5895b',
    'src/trading_runtime/confirmed_original_risk_failure.py': '653f8f54f8800c9b9c8dac7804fd6ef2d1ae83a2a8785aef258f0787c1413197',
    'src/backend/backtest_confirmed_original_risk_source.py': '0df8a2bda59e1ce95b150ca9ff43a22a2752f5a602856eaeee0cf6d866dac6f0',
    'src/trading_runtime/arte_original_risk_diagnostic_v4.py': 'c048997055bb65060ca280d197b986994a4a4c1fee2eca883ec2c5cc9fbe5a51',
    'src/trading_runtime/original_risk_diagnostic_profile.py': '595f15675892f4b9f59cb1ee1b19536e59ab47ca563f55971b39811614a4298c',
    'src/backend/backtest_v4_performance_evidence.py': 'c4f6462d119618c925f11ea614fa8778e91487844adf6cd686160b4ea1c52b09',
    'scripts/clickhouse/provision_backtest_v4_original_risk_runner.py': '7c6913752693ab696804b3968a5efffd89a0a5496a9e65b0f8a3312b1e4fb46d',
    'src/backend/backtest_market_data.py': 'ad4135f16a0979b5af821b0c00c294511635b3b84b37b90f7bdff79e503cf9d3',
    'src/backend/backtest_declared_ladder_plan.py': {'automatic_policy': 'f5383587a2b55fcd05c6c6f0872d7e641b1059d6ee229094f5a10ef15b5c2669'},
    'src/backend/backtest_ladder_source_authority.py': {'declared_ladder_policy': '56c0bef2dbea0bccd752d45293520c1ce8ebec0b3e2829c48c5a2a7eccbe360c'},
    'src/trading_runtime/squeeze_ladder_automatic.py': {'AutomaticLadderPolicy': '8e6b530d31208cc0c139f51eee83da902e67a16f532118b6f7eacc90a30f936f'},
    'src/trading_runtime/original_risk_checkpoint.py': '9049359bdf28ddbbbfdebeaa31cf48dfe4fd035cae561869f0a1e831ed4a1cee',
    'src/trading_runtime/original_risk_pending_snapshot.py': 'bef2be65cd8fa6d02a435bc8290b3e765bb496567e3abb7b3d7fd912311239a7',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'a6011032b9690f0ddb72e8db54401e57e7850431e243023f3c3b8d90d524643c',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
    'src/trading_runtime/strategy_sixty_eight_release.py': '2d20f96d7c51e19d1a131fc4dd68d9ff66dcf9c76f2d9627e8b3954cc72c780f',
    'src/trading_runtime/strategy_sixty_eight_contract.py': 'a7a4f50af9116c58da3f4d835d738b4ef7774165d6d87aafa04f6d5cd845205d',
}


def certify_strategy_sixty_nine_source(*, source_overrides=None):
    """Verify ordered declarations, their anchor and fresh native source bytes."""
    self_relative = 'src/backend/backtest_strategy_sixty_nine_certification.py'
    symbol_leaves = {
        'src/backend/backtest_fixed_v4_certification.py': ('certify_numbered_fixed_v4_projection',),
        'src/backend/backtest_strategy_sixty_nine_certification.py': ('certify_strategy_sixty_nine_source',),
        'src/backend/backtest_declared_ladder_plan.py': ('automatic_policy',),
        'src/backend/backtest_ladder_source_authority.py': ('declared_ladder_policy',),
        'src/trading_runtime/squeeze_ladder_automatic.py': ('AutomaticLadderPolicy',),
    }
    metadata_anchor = 'b06378a00b1160d84737b20f8b483e77fbde47c1042609e02cd6c42caa990eeb'
    overrides = {} if source_overrides is None else source_overrides
    if type(overrides) is not dict or set(overrides) - set(REQUIRED_SOURCE_FILES):
        raise ValueError('Strategy69 source override is outside complete authority')
    if (type(REQUIRED_SOURCE_FILES) is not tuple
            or any(type(path) is not str for path in REQUIRED_SOURCE_FILES)
            or len(set(REQUIRED_SOURCE_FILES)) != len(REQUIRED_SOURCE_FILES)
            or type(STRATEGY69_SOURCE_AST) is not dict
            or tuple(STRATEGY69_SOURCE_AST) != REQUIRED_SOURCE_FILES):
        raise ValueError('Strategy69 complete source review is not sealed')
    for relative, expected in STRATEGY69_SOURCE_AST.items():
        if relative in symbol_leaves:
            if type(expected) is not dict or tuple(expected) != symbol_leaves[relative]:
                raise ValueError('Strategy69 source selector shape changed: '+relative)
            values = tuple(expected.values())
        else:
            if type(expected) is not str:
                raise ValueError('Strategy69 source digest shape changed: '+relative)
            values = (expected,)
        if any(type(value) is not str or len(value) != 64
               or any(c not in '0123456789abcdef' for c in value) for value in values):
            raise ValueError('Strategy69 source digest is malformed: '+relative)
    metadata = dict(required=REQUIRED_SOURCE_FILES,
        sources={path:value for path,value in STRATEGY69_SOURCE_AST.items() if path != self_relative},
        symbols=symbol_leaves)
    if sha256(json.dumps(metadata,sort_keys=True,separators=(',', ':')).encode()).hexdigest() != metadata_anchor:
        raise ValueError('Strategy69 source metadata anchor changed')
    root = Path(__file__).parents[2]
    observed = []
    for relative in REQUIRED_SOURCE_FILES:
        expected = STRATEGY69_SOURCE_AST[relative]
        source = Path(overrides.get(relative, root/relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Strategy69 source cannot be parsed: '+relative) from exc
        if relative in symbol_leaves:
            for symbol, digest in expected.items():
                kind = ast.ClassDef if relative == 'src/trading_runtime/squeeze_ladder_automatic.py' else ast.FunctionDef
                nodes = [node for node in tree.body if isinstance(node, kind) and node.name == symbol]
                if len(nodes) != 1 or sha256(ast.unparse(nodes[0]).encode()).hexdigest() != digest:
                    raise ValueError('Strategy69 pinned source changed: '+relative+':'+symbol)
        elif sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy69 pinned source changed: '+relative)
        if relative == self_relative:
            envelope = tree.body
            imports = ('from hashlib import sha256', 'import json',
                       'from pathlib import Path', 'import ast')
            if (len(envelope) != 8
                    or not isinstance(envelope[0],ast.Expr)
                    or not isinstance(envelope[0].value,ast.Constant)
                    or envelope[0].value.value != 'Closed selected consecutive original-risk failure source authority; publication is separate.'
                    or tuple(ast.unparse(node) for node in envelope[1:5]) != imports
                    or any(not isinstance(node,ast.Assign) or len(node.targets) != 1
                           or not isinstance(node.targets[0],ast.Name)
                           for node in envelope[5:7])
                    or tuple(node.targets[0].id for node in envelope[5:7])
                       != ('REQUIRED_SOURCE_FILES','STRATEGY69_SOURCE_AST')
                    or not isinstance(envelope[7],ast.FunctionDef)
                    or envelope[7].name != 'certify_strategy_sixty_nine_source'):
                raise ValueError('Strategy69 own source module envelope changed')
            declarations = {}
            for node in tree.body:
                if (isinstance(node,ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0],ast.Name)
                        and node.targets[0].id in ('REQUIRED_SOURCE_FILES','STRATEGY69_SOURCE_AST')):
                    name = node.targets[0].id
                    if name in declarations:
                        raise ValueError('Strategy69 fresh source declaration duplicated')
                    declarations[name] = ast.literal_eval(node.value)
            if (set(declarations) != {'REQUIRED_SOURCE_FILES','STRATEGY69_SOURCE_AST'}
                    or declarations['REQUIRED_SOURCE_FILES'] != REQUIRED_SOURCE_FILES
                    or declarations['STRATEGY69_SOURCE_AST'] != STRATEGY69_SOURCE_AST):
                raise ValueError('Strategy69 fresh source declarations differ')
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed,separators=(',', ':')).encode()).hexdigest()
