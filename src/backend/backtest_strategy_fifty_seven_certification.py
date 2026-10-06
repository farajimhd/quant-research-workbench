"""Full inherited50 declared entry spread risk proof; closed until source review."""
import ast
from src.backend.source_ast_summary import canonical_module_ast_digest
from hashlib import sha256
import json
from pathlib import Path

REQUIRED_SOURCE_FILES = ('pipelines/strategy_one/configuration_publisher.py',
 'pipelines/strategy_one/strategy_fifty_seven_configuration.py',
 'scripts/clickhouse/install_trading_journal_layout.py',
 'scripts/clickhouse/plan_trading_journal_layout.py',
 'scripts/clickhouse/provision_backtest_v4_entry_cost_runner.py',
 'scripts/clickhouse/publish_strategy_fifty_seven_configuration.py',
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
 'src/trading_runtime/strategy_fifty_release.py',
 'src/trading_runtime/strategy_fifty_seven_contract.py',
 'src/trading_runtime/strategy_fifty_seven_release.py',
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
 'src/trading_runtime/squeeze_ladder_geometry.py')

STRATEGY57_SOURCE_AST = {'pipelines/strategy_one/configuration_publisher.py': 'b9ed0a45ef7869f2889392413aa44865bab3d564da9c52d0f980e5620eaf8c5d', 'pipelines/strategy_one/strategy_fifty_seven_configuration.py': '0f8c77de7b7b8c7f6b9d85f152921afc5f9fbbe612f791dec5f2e8593c405f48', 'scripts/clickhouse/install_trading_journal_layout.py': 'b7dad511f062c717beee0ff60fbc6b4eba19cc9fab34d1735c07ee279dda00b8', 'scripts/clickhouse/plan_trading_journal_layout.py': '0c811495a1a760a1a0ecc254ecd1705a12c94110dc0e5c33b992704fc7765f6d', 'scripts/clickhouse/provision_backtest_v4_entry_cost_runner.py': '59eb0fa003f4f41e1add1ff409ea3661c4372987c8424f9d9d8550e793b62646', 'scripts/clickhouse/publish_strategy_fifty_seven_configuration.py': '281001b8332ae2a907d66e8caa1684e79ddbdc86745c93388998b864c8d80311', 'scripts/clickhouse/report_strategy_one_trades.py': '04d1b0a3c0a9bd58785d8753c87b4207f225fc1c0636081a22ad0662a5a925f4', 'src/backend/backtest_entry_spread_risk.py': 'e1ca99c0382dd355000b122742acbc3fd3ff0a703f5204172eee76584cc44a1d', 'src/backend/backtest_entry_spread_risk_v2.py': '0287cbbb1d9795121b23f1fa666e128135001cb1129c9b5b16cc0ac951e13cb9', 'src/backend/backtest_declared_entry_quote_source.py': '7a812908fd044f2573d525ac54b59383ab241ca673dbe8194ef30b12c79259e7', 'src/backend/backtest_fixed_journal_bootstrap.py': 'a9923563b6be510187d171e8a13413c638228b0672c07e88bccc7fd146b00837', 'src/backend/backtest_fixed_v4_certification.py': 'de597164eec99f389e8ffd20a3df111ba3d141218102966851ead529a2e48617', 'src/backend/backtest_journal_memory.py': '6c31e1a22ea4ae131363e4a7b630f0edeb0ab14bb0c0c641cd4ecb8175ba2864', 'src/backend/backtest_strategy_certified_price_break.py': 'e1fa545fbbd95b4784e4cf2e0ef320276a4ff525683f680ddea6fc0ccd4c1a38', 'src/backend/backtest_strategy_episode_activity_source.py': '2d4fa40c46a866be83328ce7443a350655620bf9c4b4a82044dc45ee7441c8c0', 'src/backend/backtest_strategy_liquidity_fade.py': '4870509aaef5af9a6b45d7a9c389dfe737a927f0beb71164b00ef9919d1a7e24', 'src/backend/backtest_strategy_liquidity_fade_loader.py': 'd6e5660d40bb75df739f8fe862ac7a8ed8441cdd7c58da64184f219d5d8a62b4', 'src/backend/backtest_strategy_one_configuration.py': '5eeb431d724b2d5417f7017ff8ccf39ee80b38b5a4aeafd757a3a86db5ef736e', 'src/backend/backtest_strategy_one_coordinator.py': '7d40c9a3d75872e1ff263729eb578cf9860e913bae3863ec8889ca999e5df334', 'src/backend/backtest_strategy_one_execution.py': '637da264cea91bcf316473704933e6b8a0932b43509a6d5a3ffb9809451a9a93', 'src/backend/backtest_strategy_one_management.py': 'f57ee3ecf81e70f84916fbbd84e2c827f38b510d8c1cce2d11a7ad2007d8a33e', 'src/backend/backtest_typed_projection.py': '942a03a57b59c9371c1fb2f62310ab31706d56f1e2a5f2326ba3efb745e9908c', 'src/backend/backtest_typed_publisher.py': '24e615607412b1a477f405a4188c24db67b69031a74965de0fbc189ee369c218', 'src/backend/backtest_v4_saved_review.py': 'e75fcb1c1fbb4689f5144d5964248b8e2e2402428048d5afb6177d986620664e', 'src/backend/replay_run_service.py': '1871d07a8d40f07def8accc0811ca228320d49940a9a122f8f71c496e5831440', 'src/trading_runtime/arte_confirmed_ah_failure_v4.py': '5f8b90176e9bbe6232a1f7b88a86333cc2466dc5a932740d27b7cbf4d4345435', 'src/trading_runtime/arte_entry_activity_v4.py': '95b32e75b49992cb38e0569e1576e093841f46eb3b8defd37e5ec4d0e8494b92', 'src/trading_runtime/arte_entry_spread_risk_v4.py': '936b3a9b67c7d9ad9be63a0b44b6d9255e0275941f9c4ef95eaf2779b1c6f3e6', 'src/trading_runtime/arte_first_price_entry_v4.py': 'c968e50cb42d4326f9499b20134ec6500990058a6415aed581f373fb2d2efec6', 'src/trading_runtime/arte_followthrough_failure_v4.py': '13377a8b4d30488440076af50949db7f4a54c612d02e10391efbf4edb54f68c9', 'src/trading_runtime/arte_initial_momentum_entry_v4.py': '037069b752fbc7af9de87ba44f53d3af79833cdb379872ce2554013456af21ea', 'src/trading_runtime/arte_journal_compound_v4.py': 'c270c28e1fa80d4ff0da67d1b2cd64f8c6b402c7db812caa2fb2d8399010d9e7', 'src/trading_runtime/arte_journal_commit_v4.py': 'b554846e3a213c17818c6e89cfb3baee1f0738834caf3dc0d0d3b526b16d0e22', 'src/trading_runtime/arte_journal_writer.py': '3040cb3e96f6fc0448ce7ab039d1dd08fbaa37126777e9954c4a5b33aac13a31', 'src/trading_runtime/arte_liquidity_fade_failure_v4.py': '216e413631d81e7cb0b29d7103ab1939320946a43a6efeba17c646cdc6147134', 'src/trading_runtime/arte_oms_projection.py': 'a4514545219759a1ae0c79465531e548ec2e34584c61d1fb7abc09da130893f9', 'src/trading_runtime/arte_profit_giveback_v4.py': '3e22f8f0c57d85a471cd15a63e18295517c617f9b8dc88eae5d1cc7908a70293', 'src/trading_runtime/arte_rising_momentum_entry_v4.py': '2f9b2689448c0592d99134286955618d0c312c29d8712d37f79310bffbb55d1d', 'src/trading_runtime/arte_strategy_one_entry_journal.py': '45dc2b6abf7f528fbf1e056c5249a78eb1b52813f051a732512712abc5993856', 'src/trading_runtime/declared_followthrough_failure.py': '68c2ed0f236db57cf07b557a331448b77a35de9c0645f5e7ec5adde51e1f9920', 'src/trading_runtime/declared_profit_giveback.py': '944de8d53f75e5f270120fa36d5d8786a6ea167ed824f1d8e501c8a9ae120bc3', 'src/trading_runtime/early_original_risk_failure.py': '1df39278ef45fd7392fa570702b836588c21274aa0eb36c3667477dbcef0dc89', 'src/trading_runtime/entry_spread_risk.py': 'cbb5fcbae0b6c6581a3dd9a2d59dd6dbc57372799f2781e3d69c4559b5a6be8e', 'src/trading_runtime/numbered_fixed_strategy.py': '4efe657dd24b4d1f7ac87df05f0fabf5538c163247a99bc68f6674da69b3b46f', 'src/trading_runtime/runtime.py': '57af631c859aa491b604830bb552f47abdc3e50e8a94d2b7fbeec234133fa465', 'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '5fce50ffc270e19c98f200dbe949edd9ce718959d8f7cb48324dce5f1e96590f', 'src/trading_runtime/strategy_confirmed_ah_failure_source.py': '5b5fbaeda54d8e7593d6646c7d6631ab8323ee472cdf73ecee947e049a77b50a', 'src/trading_runtime/strategy_fifty_release.py': '2f078566963e645c725d14526621e87a99d623f415fa010aa2440921e8d87f1b', 'src/trading_runtime/strategy_fifty_seven_contract.py': '155e60d2575186e9b3e74fbee53e58a77dcb54075b22ce0693542a8e953622a1', 'src/trading_runtime/strategy_fifty_seven_release.py': 'c3c0c3a663787478c80d4b0a3ae4f038f26b100ff405d814ab9aac622afe26ea', 'src/trading_runtime/strategy_followthrough_exit.py': 'b39c0a150a26245ced193548897600c3dbbff8951271a8013e73832611855d66', 'src/trading_runtime/strategy_half_risk_liquidity_fade.py': '3f8d8949125090eda81d3256cae65920f950da12cbbb88dc6fb30a922c4cbe21', 'src/trading_runtime/strategy_liquidity_fade_exit.py': '3ccbc3b5f09d6553a23372e9a2c435c936defa2dc800717d36f2824c21a04325', 'src/trading_runtime/strategy_liquidity_fade_publication.py': '3ae4e11e8b2d2d9f470c4beab7c0af09b0259c1168a367f728778ed8accb6827', 'src/trading_runtime/strategy_liquidity_fade_source.py': '0a92702b17b61fba3112bf040afa8bc764ca3df2656ef25bd90130b13714b189', 'src/trading_runtime/strategy_one_management_snapshot.py': 'c6bc24067dac5501499bcf1bdf92d84b45361776ee43bd104d5e40c95e2d1f05', 'src/trading_runtime/strategy_profit_giveback.py': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10', 'src/trading_runtime/strategy_profit_giveback_arm.py': '5757092f1089572ad5ae39a6c482932fc8629930413e48b5c6944dc742d2358c', 'src/trading_runtime/strategy_profit_giveback_exit.py': 'c8466273babe33b6c54ed898d446a6b1b68297fd2e37e69b27270a4bc804b49e', 'src/trading_runtime/strategy_profit_giveback_source.py': '1ed0dcde9e4a462519e773069686a1a95731534312d6a5a290fa351290aefa29', 'src/trading_runtime/strategy_registry.py': '2b4fe162b5cef41b16993848121bbe03ee55902164189da195f3b2dd10c6d188', 'src/trading_runtime/strategy_rising_momentum_witness.py': '8433d7be44361854221822708c23dbbc7899b106a16aa5d9f62d2e1825d6bf30', 'src/trading_runtime/entry_momentum_growth.py': '8acec288e9cf7d1c62bf8cb2fa386f82d5db69a63fc9306b90168f8cee3c0b5c', 'src/backend/backtest_declared_initial_momentum.py': '786a12c62a157c092652c8cd7d695fe4f5e8ebfb315c9b8dbab56580fb0a4b81', 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995', 'src/trading_runtime/squeeze_ladder_geometry.py': '34658cac7dba475b19ca3963bbcad1397a88f5f86e4133664947c9e656671e0d'}


def certify_strategy_fifty_seven_source(*, source_overrides=None):
    overrides = source_overrides or {}
    if set(STRATEGY57_SOURCE_AST) != set(REQUIRED_SOURCE_FILES):
        raise ValueError('Strategy57 complete source review is not sealed')
    if set(overrides) - set(STRATEGY57_SOURCE_AST):
        raise ValueError('Strategy57 source override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in STRATEGY57_SOURCE_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        try:
            actual = canonical_module_ast_digest(source)
        except SyntaxError as exc:
            raise ValueError('Strategy57 source cannot be parsed: ' + relative) from exc
        if actual != expected:
            raise ValueError('Strategy57 pinned source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
