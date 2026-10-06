"""Closed selected all-held failure source authority; publication is separate."""
from hashlib import sha256
import json
from pathlib import Path
import ast

REQUIRED_SOURCE_FILES = (
    'pipelines/strategy_one/configuration_publisher.py',
    'pipelines/strategy_one/strategy_sixty_six_configuration.py',
    'scripts/clickhouse/install_trading_journal_layout.py',
    'scripts/clickhouse/plan_trading_journal_layout.py',
    'scripts/clickhouse/provision_backtest_v4_runner.py',
    'scripts/clickhouse/publish_strategy_sixty_six_configuration.py',
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
    'src/trading_runtime/strategy_sixty_six_contract.py',
    'src/trading_runtime/strategy_sixty_six_release.py',
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
    'src/backend/backtest_strategy_sixty_six_certification.py',
    'src/trading_runtime/clickhouse_transport.py',
    'research/mlops/clickhouse.py',
    'research/mlops/env.py',
)
STRATEGY66_SOURCE_AST = {
    'pipelines/strategy_one/configuration_publisher.py': 'd6eedccd686e0d26b39153cf8cb07d753f9fae1f77818cf367b0e30ef6570de1',
    'pipelines/strategy_one/strategy_sixty_six_configuration.py': '7c01df34cc9466bdf366bbeda1cb0b7dbdf33c62114f2ce0f1c6d659a763c041',
    'scripts/clickhouse/install_trading_journal_layout.py': 'b7dad511f062c717beee0ff60fbc6b4eba19cc9fab34d1735c07ee279dda00b8',
    'scripts/clickhouse/plan_trading_journal_layout.py': '0c811495a1a760a1a0ecc254ecd1705a12c94110dc0e5c33b992704fc7765f6d',
    'scripts/clickhouse/provision_backtest_v4_runner.py': 'a97b2323c1104b0c81396cc9ad5999d5962ce44bffc8617916ff9f67993fe056',
    'scripts/clickhouse/publish_strategy_sixty_six_configuration.py': '7e213a1c384203ddf8c2d3df43cf977b171481561b7e2d8e6d1b4a16154797c9',
    'scripts/clickhouse/report_strategy_one_trades.py': '04d1b0a3c0a9bd58785d8753c87b4207f225fc1c0636081a22ad0662a5a925f4',
    'src/backend/backtest_declared_entry_quote_source.py': '7a812908fd044f2573d525ac54b59383ab241ca673dbe8194ef30b12c79259e7',
    'src/backend/backtest_declared_initial_momentum.py': '786a12c62a157c092652c8cd7d695fe4f5e8ebfb315c9b8dbab56580fb0a4b81',
    'src/backend/backtest_entry_spread_risk.py': 'e1ca99c0382dd355000b122742acbc3fd3ff0a703f5204172eee76584cc44a1d',
    'src/backend/backtest_entry_spread_risk_v2.py': '0287cbbb1d9795121b23f1fa666e128135001cb1129c9b5b16cc0ac951e13cb9',
    'src/backend/backtest_fixed_journal_bootstrap.py': 'a9923563b6be510187d171e8a13413c638228b0672c07e88bccc7fd146b00837',
    'src/backend/backtest_fixed_v4_certification.py': {'certify_numbered_fixed_v4_projection': '7354042a5ae61438cbd56a8db4bd10aa8fff9453d22a029f8bb8ae3a3a1bc52d'},
    'src/backend/backtest_journal_memory.py': '6c31e1a22ea4ae131363e4a7b630f0edeb0ab14bb0c0c641cd4ecb8175ba2864',
    'src/backend/backtest_strategy_certified_price_break.py': 'e1fa545fbbd95b4784e4cf2e0ef320276a4ff525683f680ddea6fc0ccd4c1a38',
    'src/backend/backtest_strategy_episode_activity_source.py': '2d4fa40c46a866be83328ce7443a350655620bf9c4b4a82044dc45ee7441c8c0',
    'src/backend/backtest_strategy_liquidity_fade.py': '4870509aaef5af9a6b45d7a9c389dfe737a927f0beb71164b00ef9919d1a7e24',
    'src/backend/backtest_strategy_liquidity_fade_loader.py': 'd6e5660d40bb75df739f8fe862ac7a8ed8441cdd7c58da64184f219d5d8a62b4',
    'src/backend/backtest_strategy_one_configuration.py': '6514b72bbb4dc542b958d5d59def72f62491a2ce08acf813f010367e8ce776bb',
    'src/backend/backtest_strategy_one_coordinator.py': '7d40c9a3d75872e1ff263729eb578cf9860e913bae3863ec8889ca999e5df334',
    'src/backend/backtest_strategy_one_execution.py': '637da264cea91bcf316473704933e6b8a0932b43509a6d5a3ffb9809451a9a93',
    'src/backend/backtest_strategy_one_management.py': '96c4bc9c6ac5e3041bba14bb382565395d4431549f1e3de98dcb7c8c582c1e1e',
    'src/backend/backtest_typed_projection.py': '942a03a57b59c9371c1fb2f62310ab31706d56f1e2a5f2326ba3efb745e9908c',
    'src/backend/backtest_typed_publisher.py': '24e615607412b1a477f405a4188c24db67b69031a74965de0fbc189ee369c218',
    'src/backend/backtest_v4_saved_review.py': 'e75fcb1c1fbb4689f5144d5964248b8e2e2402428048d5afb6177d986620664e',
    'src/backend/replay_run_service.py': '1871d07a8d40f07def8accc0811ca228320d49940a9a122f8f71c496e5831440',
    'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995',
    'src/trading_runtime/arte_confirmed_ah_failure_v4.py': '5f8b90176e9bbe6232a1f7b88a86333cc2466dc5a932740d27b7cbf4d4345435',
    'src/trading_runtime/arte_entry_activity_v4.py': '95b32e75b49992cb38e0569e1576e093841f46eb3b8defd37e5ec4d0e8494b92',
    'src/trading_runtime/arte_entry_spread_risk_v4.py': '936b3a9b67c7d9ad9be63a0b44b6d9255e0275941f9c4ef95eaf2779b1c6f3e6',
    'src/trading_runtime/arte_first_price_entry_v4.py': 'c968e50cb42d4326f9499b20134ec6500990058a6415aed581f373fb2d2efec6',
    'src/trading_runtime/arte_followthrough_failure_v4.py': '13377a8b4d30488440076af50949db7f4a54c612d02e10391efbf4edb54f68c9',
    'src/trading_runtime/arte_initial_momentum_entry_v4.py': '037069b752fbc7af9de87ba44f53d3af79833cdb379872ce2554013456af21ea',
    'src/trading_runtime/arte_journal_commit_v4.py': 'b554846e3a213c17818c6e89cfb3baee1f0738834caf3dc0d0d3b526b16d0e22',
    'src/trading_runtime/arte_journal_compound_v4.py': 'c270c28e1fa80d4ff0da67d1b2cd64f8c6b402c7db812caa2fb2d8399010d9e7',
    'src/trading_runtime/arte_journal_writer.py': '3040cb3e96f6fc0448ce7ab039d1dd08fbaa37126777e9954c4a5b33aac13a31',
    'src/trading_runtime/arte_liquidity_fade_failure_v4.py': '216e413631d81e7cb0b29d7103ab1939320946a43a6efeba17c646cdc6147134',
    'src/trading_runtime/arte_oms_projection.py': 'a4514545219759a1ae0c79465531e548ec2e34584c61d1fb7abc09da130893f9',
    'src/trading_runtime/arte_profit_giveback_v4.py': '3e22f8f0c57d85a471cd15a63e18295517c617f9b8dc88eae5d1cc7908a70293',
    'src/trading_runtime/arte_rising_momentum_entry_v4.py': '2f9b2689448c0592d99134286955618d0c312c29d8712d37f79310bffbb55d1d',
    'src/trading_runtime/arte_strategy_one_entry_journal.py': '45dc2b6abf7f528fbf1e056c5249a78eb1b52813f051a732512712abc5993856',
    'src/trading_runtime/declared_followthrough_failure.py': 'ad60271c51250ee004e00e5fc00f8bd8e220794968a9421ec0ff73bd002d5c98',
    'src/trading_runtime/declared_profit_giveback.py': '944de8d53f75e5f270120fa36d5d8786a6ea167ed824f1d8e501c8a9ae120bc3',
    'src/trading_runtime/early_original_risk_failure.py': '1df39278ef45fd7392fa570702b836588c21274aa0eb36c3667477dbcef0dc89',
    'src/trading_runtime/entry_momentum_growth.py': '8acec288e9cf7d1c62bf8cb2fa386f82d5db69a63fc9306b90168f8cee3c0b5c',
    'src/trading_runtime/entry_spread_risk.py': 'cbb5fcbae0b6c6581a3dd9a2d59dd6dbc57372799f2781e3d69c4559b5a6be8e',
    'src/trading_runtime/numbered_fixed_strategy.py': '3292fce6d35bcf81fc3ce284d47ce133b49dbc528c902178f15f40d27cc3bc46',
    'src/trading_runtime/runtime.py': '57af631c859aa491b604830bb552f47abdc3e50e8a94d2b7fbeec234133fa465',
    'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '5fce50ffc270e19c98f200dbe949edd9ce718959d8f7cb48324dce5f1e96590f',
    'src/trading_runtime/strategy_confirmed_ah_failure_source.py': '5b5fbaeda54d8e7593d6646c7d6631ab8323ee472cdf73ecee947e049a77b50a',
    'src/trading_runtime/strategy_forty_two_release.py': 'ae2e532ddaf0adc3b40ad77a40f8d3fd9cbd6154c344671a17e66545cc81656f',
    'src/trading_runtime/strategy_sixty_six_contract.py': '0c175a1e72ca0f613f2da77c05942055f3d65f7fec8d6e14d1aec88297b8a9d2',
    'src/trading_runtime/strategy_sixty_six_release.py': '9a55a0d5bd44e241c0df0717feeb2bf8134a9bb814e1121f8337197ce1bc1ed5',
    'src/trading_runtime/strategy_followthrough_exit.py': 'c3e1a4cb00ae48d3f2e11ffb6218dc26a411bc254b6700f36cb613a1bef32d91',
    'src/trading_runtime/strategy_half_risk_liquidity_fade.py': '3f8d8949125090eda81d3256cae65920f950da12cbbb88dc6fb30a922c4cbe21',
    'src/trading_runtime/strategy_liquidity_fade_exit.py': '3ccbc3b5f09d6553a23372e9a2c435c936defa2dc800717d36f2824c21a04325',
    'src/trading_runtime/strategy_liquidity_fade_publication.py': '3ae4e11e8b2d2d9f470c4beab7c0af09b0259c1168a367f728778ed8accb6827',
    'src/trading_runtime/strategy_liquidity_fade_source.py': '0a92702b17b61fba3112bf040afa8bc764ca3df2656ef25bd90130b13714b189',
    'src/trading_runtime/strategy_one_management_snapshot.py': 'c6bc24067dac5501499bcf1bdf92d84b45361776ee43bd104d5e40c95e2d1f05',
    'src/trading_runtime/strategy_profit_giveback.py': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10',
    'src/trading_runtime/strategy_profit_giveback_arm.py': '5757092f1089572ad5ae39a6c482932fc8629930413e48b5c6944dc742d2358c',
    'src/trading_runtime/strategy_profit_giveback_exit.py': 'c8466273babe33b6c54ed898d446a6b1b68297fd2e37e69b27270a4bc804b49e',
    'src/trading_runtime/strategy_profit_giveback_source.py': '1ed0dcde9e4a462519e773069686a1a95731534312d6a5a290fa351290aefa29',
    'src/trading_runtime/strategy_registry.py': '708960fc0d38a859ae97d58f7feac74aa8c8087671b826d3acae0eb4d15a0a9f',
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
    'src/trading_runtime/arte_typed_insert_dispatch.py': '9d5cc6c222a11237b11bf07a25692ea15cf008ed9e15fc1146062d237e12a639',
    'src/trading_runtime/journal_contract.py': '130e8b70f3708cd30f0524b035a0c99cded407bf6b1fc39a610cb7e19458da28',
    'src/trading_runtime/strategy_one_configuration_tree.py': '44f46197d4075bbdae4d39c7f4b8160fc6ae4895e11a90f13db927f4268b7e06',
    'src/trading_runtime/strategy_engine.py': '48f5a2379095297984334ac046dabd8e36c8c3288434852f8454aed52cc8eaa6',
    'src/trading_runtime/keeper_session.py': '316240cc8dde1b4047d618efc89fc8e5bd281770e4db5d9c1267f2147fb06a18',
    'src/trading_runtime/keeper_ownership.py': '4d50a7acea06377df65ab79c506685c8ce426d1981e867049341c93e3047c2f7',
    'scripts/clickhouse/provision_fixed_backtest_v3_principals.py': '77df1f30c5fb6fb5700f2b8843a2d71a0f801beb27026371678666e5d228a6ee',
    'scripts/clickhouse/provision_trading_journal.py': 'fb0a05f5a3098bcd28482db4b7f34dc3a47e3ac436bca0b0ca747262dd1e507e',
    'src/backend/backtest_strategy_sixty_six_certification.py': {'certify_strategy_sixty_six_source': '161ed4c55b42dfb43d47c208b6e74782e7c8e38247c8b389240113a5014e633d'},
    'src/trading_runtime/clickhouse_transport.py': '108e4e9a4e757ed792ab01f8d7faefdc035b2da398cfcbdd8dc3917c20cbb5df',
    'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
    'research/mlops/env.py': '856dc976500cfd9efc0648b22a28665951f8bdeb81328c45a47f5dc586b5895b',
}


def certify_strategy_sixty_six_source(*, source_overrides=None):
    """Verify ordered declarations, their anchor and fresh native source bytes."""
    self_relative = 'src/backend/backtest_strategy_sixty_six_certification.py'
    symbol_leaves = {
        'src/backend/backtest_fixed_v4_certification.py': ('certify_numbered_fixed_v4_projection',),
        'src/backend/backtest_strategy_sixty_six_certification.py': ('certify_strategy_sixty_six_source',),
    }
    metadata_anchor = 'b6aa93e27f141f860b7b602a8970214adae3be3a0ad5bc60720781bb2e093c9d'
    overrides = {} if source_overrides is None else source_overrides
    if type(overrides) is not dict or set(overrides) - set(REQUIRED_SOURCE_FILES):
        raise ValueError('Strategy66 source override is outside complete authority')
    if (type(REQUIRED_SOURCE_FILES) is not tuple
            or any(type(path) is not str for path in REQUIRED_SOURCE_FILES)
            or len(set(REQUIRED_SOURCE_FILES)) != len(REQUIRED_SOURCE_FILES)
            or type(STRATEGY66_SOURCE_AST) is not dict
            or tuple(STRATEGY66_SOURCE_AST) != REQUIRED_SOURCE_FILES):
        raise ValueError('Strategy66 complete source review is not sealed')
    for relative, expected in STRATEGY66_SOURCE_AST.items():
        if relative in symbol_leaves:
            if type(expected) is not dict or tuple(expected) != symbol_leaves[relative]:
                raise ValueError('Strategy66 source selector shape changed: '+relative)
            values = tuple(expected.values())
        else:
            if type(expected) is not str:
                raise ValueError('Strategy66 source digest shape changed: '+relative)
            values = (expected,)
        if any(type(value) is not str or len(value) != 64
               or any(c not in '0123456789abcdef' for c in value) for value in values):
            raise ValueError('Strategy66 source digest is malformed: '+relative)
    metadata = dict(required=REQUIRED_SOURCE_FILES,
        sources={path:value for path,value in STRATEGY66_SOURCE_AST.items() if path != self_relative},
        symbols=symbol_leaves)
    if sha256(json.dumps(metadata,sort_keys=True,separators=(',', ':')).encode()).hexdigest() != metadata_anchor:
        raise ValueError('Strategy66 source metadata anchor changed')
    root = Path(__file__).parents[2]
    observed = []
    for relative in REQUIRED_SOURCE_FILES:
        expected = STRATEGY66_SOURCE_AST[relative]
        source = Path(overrides.get(relative, root/relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Strategy66 source cannot be parsed: '+relative) from exc
        if relative in symbol_leaves:
            for symbol, digest in expected.items():
                nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == symbol]
                if len(nodes) != 1 or sha256(ast.unparse(nodes[0]).encode()).hexdigest() != digest:
                    raise ValueError('Strategy66 pinned source changed: '+relative+':'+symbol)
        elif sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy66 pinned source changed: '+relative)
        if relative == self_relative:
            envelope = tree.body
            imports = ('from hashlib import sha256', 'import json',
                       'from pathlib import Path', 'import ast')
            if (len(envelope) != 8
                    or not isinstance(envelope[0],ast.Expr)
                    or not isinstance(envelope[0].value,ast.Constant)
                    or envelope[0].value.value != 'Closed selected all-held failure source authority; publication is separate.'
                    or tuple(ast.unparse(node) for node in envelope[1:5]) != imports
                    or any(not isinstance(node,ast.Assign) or len(node.targets) != 1
                           or not isinstance(node.targets[0],ast.Name)
                           for node in envelope[5:7])
                    or tuple(node.targets[0].id for node in envelope[5:7])
                       != ('REQUIRED_SOURCE_FILES','STRATEGY66_SOURCE_AST')
                    or not isinstance(envelope[7],ast.FunctionDef)
                    or envelope[7].name != 'certify_strategy_sixty_six_source'):
                raise ValueError('Strategy66 own source module envelope changed')
            declarations = {}
            for node in tree.body:
                if (isinstance(node,ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0],ast.Name)
                        and node.targets[0].id in ('REQUIRED_SOURCE_FILES','STRATEGY66_SOURCE_AST')):
                    name = node.targets[0].id
                    if name in declarations:
                        raise ValueError('Strategy66 fresh source declaration duplicated')
                    declarations[name] = ast.literal_eval(node.value)
            if (set(declarations) != {'REQUIRED_SOURCE_FILES','STRATEGY66_SOURCE_AST'}
                    or declarations['REQUIRED_SOURCE_FILES'] != REQUIRED_SOURCE_FILES
                    or declarations['STRATEGY66_SOURCE_AST'] != STRATEGY66_SOURCE_AST):
                raise ValueError('Strategy66 fresh source declarations differ')
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed,separators=(',', ':')).encode()).hexdigest()
