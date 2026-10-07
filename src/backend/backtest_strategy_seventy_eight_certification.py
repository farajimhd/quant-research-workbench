"""Closed supplemental reentry source authority; full42 composition remains mandatory."""
from hashlib import sha256
import json
from pathlib import Path
import ast

REQUIRED_SOURCE_FILES = (
    'pipelines/strategy_one/configuration_publisher.py',
    'pipelines/strategy_one/strategy_seventy_eight_configuration.py',
    'scripts/clickhouse/install_trading_journal_layout.py',
    'scripts/clickhouse/plan_trading_journal_layout.py',
    'scripts/clickhouse/provision_backtest_v4_runner.py',
    'scripts/clickhouse/publish_strategy_seventy_eight_configuration.py',
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
    'src/trading_runtime/strategy_seventy_eight_contract.py',
    'src/trading_runtime/strategy_seventy_eight_release.py',
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
    'src/backend/backtest_strategy_seventy_eight_certification.py',
    'src/trading_runtime/clickhouse_transport.py',
    'research/mlops/clickhouse.py',
    'research/mlops/env.py',
    'src/trading_runtime/confirmed_original_risk_failure.py',
    'src/trading_runtime/arte_original_risk_diagnostic_v4.py',
    'src/trading_runtime/original_risk_diagnostic_profile.py',
    'src/backend/backtest_confirmed_original_risk_source.py',
    'src/backend/backtest_market_data.py',
    'src/backend/backtest_declared_ladder_plan.py',
    'src/backend/backtest_ladder_source_authority.py',
    'src/trading_runtime/squeeze_ladder_automatic.py',
    'src/trading_runtime/original_risk_checkpoint.py',
    'src/trading_runtime/original_risk_pending_snapshot.py',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py',
    'src/trading_runtime/prior_position_high_reentry.py',
    'src/trading_runtime/prior_position_high_reentry_release.py',
    'src/trading_runtime/strategy_one_stateful.py',
    'src/backend/backtest_strategy_one_stateful.py',
    'src/backend/backtest_strategy_one_entry_product.py',
    'src/backend/backtest_strategy_one_market.py',
    'src/trading_runtime/strategy_initial_momentum_growth.py',
    'src/trading_runtime/strategy_initial_price_break.py',
    'src/trading_runtime/strategy_initial_strong_momentum.py',
    'src/trading_runtime/strategy_one_contract.py',
    'src/trading_runtime/strategy_recent_bos_entry.py',
)
STRATEGY78_SOURCE_AST = {
    'pipelines/strategy_one/configuration_publisher.py': 'c8b0f5671867a5d117328ba275c2b0b6690d72d1c5bd2c7b433efab3a2ac1f23',
    'pipelines/strategy_one/strategy_seventy_eight_configuration.py': '03d1aa91cfffe27f879418e1aff81e8e1c977eff11e14de9808157572e49e4dd',
    'scripts/clickhouse/install_trading_journal_layout.py': 'b7dad511f062c717beee0ff60fbc6b4eba19cc9fab34d1735c07ee279dda00b8',
    'scripts/clickhouse/plan_trading_journal_layout.py': '0c811495a1a760a1a0ecc254ecd1705a12c94110dc0e5c33b992704fc7765f6d',
    'scripts/clickhouse/provision_backtest_v4_runner.py': 'a97b2323c1104b0c81396cc9ad5999d5962ce44bffc8617916ff9f67993fe056',
    'scripts/clickhouse/publish_strategy_seventy_eight_configuration.py': '91768ab3fa245f419455175d802107732ac6f43b0e55b39a741a34d56e95a30a',
    'scripts/clickhouse/report_strategy_one_trades.py': '0f95db33ced0052b20f41ac655c1ee851d873344707ea6b3fd616824a65e3257',
    'src/backend/backtest_declared_entry_quote_source.py': '7a812908fd044f2573d525ac54b59383ab241ca673dbe8194ef30b12c79259e7',
    'src/backend/backtest_declared_initial_momentum.py': '786a12c62a157c092652c8cd7d695fe4f5e8ebfb315c9b8dbab56580fb0a4b81',
    'src/backend/backtest_entry_spread_risk.py': 'e1ca99c0382dd355000b122742acbc3fd3ff0a703f5204172eee76584cc44a1d',
    'src/backend/backtest_entry_spread_risk_v2.py': '0287cbbb1d9795121b23f1fa666e128135001cb1129c9b5b16cc0ac951e13cb9',
    'src/backend/backtest_fixed_journal_bootstrap.py': 'b1e67803f6ddc4b3c28e4030febd61cc8800d0f6d3e21f151b88fd24f4ce689d',
    'src/backend/backtest_fixed_v4_certification.py': '44ef7f7338ce77dfe60a086b08e67fbc5f6655ca8f4d329ff1529b8719d6518f',
    'src/backend/backtest_journal_memory.py': '7967a2dc2bd11edd2739caa04a8c27c8fb80f3d900da8bb642a5509deaf08f33',
    'src/backend/backtest_strategy_certified_price_break.py': 'e1fa545fbbd95b4784e4cf2e0ef320276a4ff525683f680ddea6fc0ccd4c1a38',
    'src/backend/backtest_strategy_episode_activity_source.py': '2d4fa40c46a866be83328ce7443a350655620bf9c4b4a82044dc45ee7441c8c0',
    'src/backend/backtest_strategy_liquidity_fade.py': '4870509aaef5af9a6b45d7a9c389dfe737a927f0beb71164b00ef9919d1a7e24',
    'src/backend/backtest_strategy_liquidity_fade_loader.py': 'd6e5660d40bb75df739f8fe862ac7a8ed8441cdd7c58da64184f219d5d8a62b4',
    'src/backend/backtest_strategy_one_configuration.py': '43211a2e24e8a6536fd3ac9bbbe38ca221f92a58d3e0a4ada3272ba34646ca90',
    'src/backend/backtest_strategy_one_coordinator.py': '7d40c9a3d75872e1ff263729eb578cf9860e913bae3863ec8889ca999e5df334',
    'src/backend/backtest_strategy_one_execution.py': 'd9ed2de2119170cf4650b139e16004c170fd2503a482831b85f40a1177ab6f8d',
    'src/backend/backtest_strategy_one_management.py': '6800d3df81562539ae5dea1ed12474f298f7a3dcf3cc16985567530601d342f5',
    'src/backend/backtest_typed_projection.py': '30e7ad90b8d41270aa80ced61ed1e9b3fdd539265ff7395b9285308237db3a9e',
    'src/backend/backtest_typed_publisher.py': '89ebb43227635813d78c07f3751cca2f93cd095c26cc1f9e5b1f809e3ca2da61',
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
    'src/trading_runtime/numbered_fixed_strategy.py': 'b37f53e1468912b2b70846bc93f24a86d9733b909562d6b5e9686545574254f8',
    'src/trading_runtime/runtime.py': '6e8c043db1d62b04560fc581e46f3f7d0382c49285975263dc6cff4b40dd8dc8',
    'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '5fce50ffc270e19c98f200dbe949edd9ce718959d8f7cb48324dce5f1e96590f',
    'src/trading_runtime/strategy_confirmed_ah_failure_source.py': '5b5fbaeda54d8e7593d6646c7d6631ab8323ee472cdf73ecee947e049a77b50a',
    'src/trading_runtime/strategy_forty_two_release.py': 'ae2e532ddaf0adc3b40ad77a40f8d3fd9cbd6154c344671a17e66545cc81656f',
    'src/trading_runtime/strategy_seventy_eight_contract.py': '1d9ae69be39c69bec946f7f43bc6abc12541fbaeea94fbb4a83d1464ff80a174',
    'src/trading_runtime/strategy_seventy_eight_release.py': '758c3fac4a65486de1991aff1f4bb46cae7cb449df77bf2b42d5efb7a4c8fd27',
    'src/trading_runtime/strategy_followthrough_exit.py': 'e5ed9aa87f606bed29e550756d11e7fa1445d9ffb47f69d4f1402bfdb17b9984',
    'src/trading_runtime/strategy_half_risk_liquidity_fade.py': '3f8d8949125090eda81d3256cae65920f950da12cbbb88dc6fb30a922c4cbe21',
    'src/trading_runtime/strategy_liquidity_fade_exit.py': '3ccbc3b5f09d6553a23372e9a2c435c936defa2dc800717d36f2824c21a04325',
    'src/trading_runtime/strategy_liquidity_fade_publication.py': '3ae4e11e8b2d2d9f470c4beab7c0af09b0259c1168a367f728778ed8accb6827',
    'src/trading_runtime/strategy_liquidity_fade_source.py': '5554b994c4ac8c566a1883c676c3c01206ee9d091ba0e10dc2c7c81890f126b2',
    'src/trading_runtime/strategy_one_management_snapshot.py': '3245f46de7e123bd9e1b3d11a55b2e60c265e2a3ddafe2b36f7b0ab48ff8ae4c',
    'src/trading_runtime/strategy_profit_giveback.py': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10',
    'src/trading_runtime/strategy_profit_giveback_arm.py': 'ef5b2892793ecf7ef2d6957a0851c1c2010e1d4da5c21c7e0dbc5de46fc39f41',
    'src/trading_runtime/strategy_profit_giveback_exit.py': 'c8466273babe33b6c54ed898d446a6b1b68297fd2e37e69b27270a4bc804b49e',
    'src/trading_runtime/strategy_profit_giveback_source.py': 'b3e58e1bf6cbb1f77a79ddfd41a8ce7115c634dc80753877549f37947db75189',
    'src/trading_runtime/strategy_registry.py': '0e51471eb4a24525235276282bba373ed3bd9c511be4132688e81f10079a1a7c',
    'src/trading_runtime/strategy_rising_momentum_witness.py': '8433d7be44361854221822708c23dbbc7899b106a16aa5d9f62d2e1825d6bf30',
    'src/trading_runtime/squeeze_ladder_geometry.py': '34658cac7dba475b19ca3963bbcad1397a88f5f86e4133664947c9e656671e0d',
    'src/trading_runtime/all_held_original_risk_failure.py': '093bb7f1b73277f21aa1a399a7b3b9f36d77dea8ca614f1130314029fcfaf8cf',
    'src/backend/backtest_fixed_run_context.py': 'b85bfc7b517a0f0fab18062f81388516d5494fb11569e1c11e0a73760f878be0',
    'src/backend/backtest_fixed_v3_preflight.py': 'a2c8e76fd5518de9abce460427a2cf860f6db5872595336a34302f50c00b2452',
    'src/backend/backtest_v4_keeper_lease.py': '7f3a0f228d1df853b9b2bf58d640eccc37b80aa7efda17f4e878b5d2614e4637',
    'src/backend/backtest_saved_source_authority.py': 'b0f17ce10cdfdbaf09315e64899dff9c6ef87b0fd520bfa334649401bcafd6bf',
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
    'src/backend/backtest_strategy_seventy_eight_certification.py': {'certify_strategy_seventy_eight_source': '101590865ba813774a46ace631a7cd1ca5495d37425e346c6c42d7684bc3f956'},
    'src/trading_runtime/clickhouse_transport.py': '108e4e9a4e757ed792ab01f8d7faefdc035b2da398cfcbdd8dc3917c20cbb5df',
    'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
    'research/mlops/env.py': '856dc976500cfd9efc0648b22a28665951f8bdeb81328c45a47f5dc586b5895b',
    'src/trading_runtime/confirmed_original_risk_failure.py': '738493dce849c08fd7e1d4f5c24a7c3188bc772a183d9083a86713177aba6f39',
    'src/trading_runtime/arte_original_risk_diagnostic_v4.py': '747bd14c7cd27adc1151770c7b946d6c14074a2e813f167dcf7e4241604176d2',
    'src/trading_runtime/original_risk_diagnostic_profile.py': '595f15675892f4b9f59cb1ee1b19536e59ab47ca563f55971b39811614a4298c',
    'src/backend/backtest_confirmed_original_risk_source.py': 'f51caf0f2197a606d02de3972aaeb41b9291dd487a2549fe2fd9987162bfa859',
    'src/backend/backtest_market_data.py': 'ad4135f16a0979b5af821b0c00c294511635b3b84b37b90f7bdff79e503cf9d3',
    'src/backend/backtest_declared_ladder_plan.py': 'a6db11ddef4ccfe9707fc60bf0e998c106eda12c84634b8caf49b2ff4e1373cb',
    'src/backend/backtest_ladder_source_authority.py': '02e9ad68a84b32da0770ba0c68d1a4fca0cc3747e78537a48da2aeb7f5d75c52',
    'src/trading_runtime/squeeze_ladder_automatic.py': '0474d98c356ea6999afe132e3295fa6e703d87d5227f7fe0186d6213e9b5b91d',
    'src/trading_runtime/original_risk_checkpoint.py': '9049359bdf28ddbbbfdebeaa31cf48dfe4fd035cae561869f0a1e831ed4a1cee',
    'src/trading_runtime/original_risk_pending_snapshot.py': '0c18cf677683921ff30f41c6b5dde78a43e5ad190e34bdde572baeabb14d9cac',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'a6011032b9690f0ddb72e8db54401e57e7850431e243023f3c3b8d90d524643c',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
    'src/trading_runtime/prior_position_high_reentry.py': '99100741f7afffd7ee8ffe94800c0c8bcfe1837aa85852ceda0749898af4df78',
    'src/trading_runtime/prior_position_high_reentry_release.py': '50704c72609c3f076164052266d2041d1c8d91c248a8badcecbf690f3197c836',
    'src/trading_runtime/strategy_one_stateful.py': 'c7e416e0b22a2c82c703f88c9442f5faa6a62e5076bdcaa9d75afebc3bee54f2',
    'src/backend/backtest_strategy_one_stateful.py': '0e2c3fc3c339c63d2ab3bfdc485a7248beb89241b0af92304ce4315ea9c116dc',
    'src/backend/backtest_strategy_one_entry_product.py': '8ffa2c1ae7d21bc8cbe7ff8079859efb8780eadd3ab82026086dee0c23b60d43',
    'src/backend/backtest_strategy_one_market.py': 'a64c8b4be6ec61bdc4d6fa92efef4a876aeac324552e9459cc5c5b1f2b403453',
    'src/trading_runtime/strategy_initial_momentum_growth.py': '68d66854b639e67a5d3734aaaf1a61ce015963d6841746a59c4ad85390761606',
    'src/trading_runtime/strategy_initial_price_break.py': '5279377acee015b28239ecd1949657fb1ce66731835a6190b686bf54a38ce3ce',
    'src/trading_runtime/strategy_initial_strong_momentum.py': '65c6021a7a7c287682a502989fe03b638ee6aaa6ae1488e9a323a0e4c75f4c06',
    'src/trading_runtime/strategy_one_contract.py': '18cec6086f5e6cc45fe68d2be6c1e04d729ef092d92a9d527f2830de0d18d277',
    'src/trading_runtime/strategy_recent_bos_entry.py': '2b264ce2911fd9a14a33c554d7ff33c22b0b097987bb9aa588e3d23425f7d490',
}


def certify_strategy_seventy_eight_source():
    """Verify exact supplemental source, envelope and fresh loaded metadata."""
    self_relative = 'src/backend/backtest_strategy_seventy_eight_certification.py'
    selectors = {'src/backend/backtest_strategy_seventy_eight_certification.py': ('certify_strategy_seventy_eight_source',)}
    metadata_anchor = 'e6157bbe6bc8d0cf8a52ec25702cdffc72308e58608f01145e48c23b3dd66fb3'
    if (type(REQUIRED_SOURCE_FILES) is not tuple or len(REQUIRED_SOURCE_FILES) != 111
            or any(type(p) is not str for p in REQUIRED_SOURCE_FILES)
            or len(set(REQUIRED_SOURCE_FILES)) != 111 or type(STRATEGY78_SOURCE_AST) is not dict
            or tuple(STRATEGY78_SOURCE_AST) != REQUIRED_SOURCE_FILES):
        raise ValueError('Strategy78 complete supplemental source is unsealed')
    for relative, value in STRATEGY78_SOURCE_AST.items():
        if relative in selectors:
            if type(value) is not dict or tuple(value) != selectors[relative]:
                raise ValueError('Strategy78 source selector shape differs: '+relative)
            values = tuple(value.values())
        else:
            if type(value) is not str:
                raise ValueError('Strategy78 source digest shape differs: '+relative)
            values = (value,)
        if any(type(v) is not str or len(v) != 64 or any(c not in '0123456789abcdef' for c in v) for v in values):
            raise ValueError('Strategy78 source digest malformed: '+relative)
    metadata = dict(required=REQUIRED_SOURCE_FILES,
        sources={p:v for p,v in STRATEGY78_SOURCE_AST.items() if p != self_relative}, selectors=selectors)
    if sha256(json.dumps(metadata,sort_keys=True,separators=(',', ':')).encode()).hexdigest() != metadata_anchor:
        raise ValueError('Strategy78 source metadata anchor differs')
    root = Path(__file__).parents[2]
    observed = []
    for relative in REQUIRED_SOURCE_FILES:
        source = (root/relative).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Strategy78 source cannot parse: '+relative) from exc
        expected = STRATEGY78_SOURCE_AST[relative]
        if relative in selectors:
            for symbol, digest in expected.items():
                kind = ast.ClassDef if relative == 'src/trading_runtime/squeeze_ladder_automatic.py' else ast.FunctionDef
                nodes = [n for n in tree.body if isinstance(n,kind) and n.name == symbol]
                if len(nodes) != 1 or sha256(ast.unparse(nodes[0]).encode()).hexdigest() != digest:
                    raise ValueError('Strategy78 pinned source changed: '+relative+':'+symbol)
        elif sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy78 pinned source changed: '+relative)
        if relative == self_relative:
            body = tree.body
            imports = ('from hashlib import sha256','import json','from pathlib import Path','import ast')
            if (len(body) != 8 or type(body[0]) is not ast.Expr
                    or type(body[0].value) is not ast.Constant
                    or body[0].value.value != 'Closed supplemental reentry source authority; full42 composition remains mandatory.'
                    or tuple(ast.unparse(n) for n in body[1:5]) != imports
                    or any(type(n) is not ast.Assign or len(n.targets) != 1 or type(n.targets[0]) is not ast.Name
                           for n in body[5:7])
                    or tuple(n.targets[0].id for n in body[5:7]) != ('REQUIRED_SOURCE_FILES','STRATEGY78_SOURCE_AST')
                    or type(body[7]) is not ast.FunctionDef or body[7].name != 'certify_strategy_seventy_eight_source'):
                raise ValueError('Strategy78 own module envelope differs')
            try:
                declared_required = ast.literal_eval(body[5].value)
                declared_pins = ast.literal_eval(body[6].value)
            except (ValueError,TypeError) as exc:
                raise ValueError('Strategy78 fresh declaration is not literal') from exc
            if declared_required != REQUIRED_SOURCE_FILES or declared_pins != STRATEGY78_SOURCE_AST:
                raise ValueError('Strategy78 fresh source differs from loaded declaration')
        observed.append((relative,sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed,separators=(',', ':')).encode()).hexdigest()
