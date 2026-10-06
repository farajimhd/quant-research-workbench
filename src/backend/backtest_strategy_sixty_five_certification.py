"""Closed selected waiting baseline source authority; publication is separate."""
from hashlib import sha256
import json
from pathlib import Path
import ast

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
 'src/backend/backtest_strategy_sixty_five_certification.py',
 'src/trading_runtime/clickhouse_transport.py',
 'research/mlops/clickhouse.py',
 'research/mlops/env.py')
STRATEGY65_SOURCE_AST = {'pipelines/strategy_one/configuration_publisher.py': 'd6eedccd686e0d26b39153cf8cb07d753f9fae1f77818cf367b0e30ef6570de1',
 'pipelines/strategy_one/strategy_sixty_five_configuration.py': 'aaadd3e34ede7385d3b36175af5549fec334bdae96dc9b64c17fe7b5c1c5a80e',
 'scripts/clickhouse/provision_backtest_v4_ladder_runner.py': 'ef34b7dfc73d05919f04b55d616f4950b7f6f3547a88e3a2d342ed2e6ab57f8e',
 'scripts/clickhouse/provision_backtest_v4_waiting_ladder_runner.py': 'f66d6eb0280ce8c7ac849784d8ab2641572eb24809a4865e5f670b34dc77e732',
 'scripts/clickhouse/publish_strategy_sixty_five_configuration.py': 'c4a0d33c4d04443f6d35baba948d3a0a48602078690c20fcd5c118d11fe644a5',
 'scripts/clickhouse/report_strategy_one_trades.py': '04d1b0a3c0a9bd58785d8753c87b4207f225fc1c0636081a22ad0662a5a925f4',
 'src/backend/backtest_declared_initial_momentum.py': '786a12c62a157c092652c8cd7d695fe4f5e8ebfb315c9b8dbab56580fb0a4b81',
 'src/backend/backtest_declared_ladder_plan.py': 'a6db11ddef4ccfe9707fc60bf0e998c106eda12c84634b8caf49b2ff4e1373cb',
 'src/backend/backtest_declared_ladder_seed.py': '01637a936e178665f35725ea18e6d6ef132ece88a3b87a7a578d32c2010f10d8',
 'src/backend/backtest_fixed_journal_bootstrap.py': 'a9923563b6be510187d171e8a13413c638228b0672c07e88bccc7fd146b00837',
 'src/backend/backtest_journal_memory.py': '6c31e1a22ea4ae131363e4a7b630f0edeb0ab14bb0c0c641cd4ecb8175ba2864',
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
 'src/backend/backtest_strategy_one_configuration.py': '7fb2771427ab2bb7e629d8d20b7208c1af7068689fb92eb16fed5a6907bd0ccd',
 'src/backend/backtest_strategy_one_coordinator.py': '7d40c9a3d75872e1ff263729eb578cf9860e913bae3863ec8889ca999e5df334',
 'src/backend/backtest_strategy_one_execution.py': '637da264cea91bcf316473704933e6b8a0932b43509a6d5a3ffb9809451a9a93',
 'src/backend/backtest_typed_projection.py': '942a03a57b59c9371c1fb2f62310ab31706d56f1e2a5f2326ba3efb745e9908c',
 'src/backend/backtest_typed_publisher.py': '24e615607412b1a477f405a4188c24db67b69031a74965de0fbc189ee369c218',
 'src/backend/backtest_v4_saved_review.py': 'e75fcb1c1fbb4689f5144d5964248b8e2e2402428048d5afb6177d986620664e',
 'src/backend/replay_run_service.py': '1871d07a8d40f07def8accc0811ca228320d49940a9a122f8f71c496e5831440',
 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995',
 'src/data_provider/calendar.py': '82706e238be3f8e13dae1177928c02a09288fed18540932dfedc11e794ca4c0e',
 'src/trading_runtime/arte_backtest_snapshot_anchor.py': '234893befe310819b6679a5541836789dc00d23b7ad9cdc10076ca3395c01087',
 'src/trading_runtime/arte_journal_commit_v4.py': 'b554846e3a213c17818c6e89cfb3baee1f0738834caf3dc0d0d3b526b16d0e22',
 'src/trading_runtime/arte_journal_writer.py': '3040cb3e96f6fc0448ce7ab039d1dd08fbaa37126777e9954c4a5b33aac13a31',
 'src/trading_runtime/arte_oms_projection.py': 'a4514545219759a1ae0c79465531e548ec2e34584c61d1fb7abc09da130893f9',
 'src/trading_runtime/arte_squeeze_ladder_schema.py': '894849755a2a22935b7d9c052dfc7b2eace9f72c73c0432f21c94880d5752c66',
 'src/trading_runtime/automatic_ladder_transport.py': 'e2b62165a417fe3465af98353c663e1417c3a7723563c45c3e63941ae722629a',
 'src/trading_runtime/entry_momentum_growth.py': '8acec288e9cf7d1c62bf8cb2fa386f82d5db69a63fc9306b90168f8cee3c0b5c',
 'src/trading_runtime/execution_policies.py': 'c0766986efe8afc2d3276e2cbb1f303a4abb99427724f2ee2c1d0a29e4e2f36c',
 'src/trading_runtime/independent_lot_protection.py': '498b88e02d483d3b8c8c327b874045df3946a80cb2cc02ac472737e1ccdd36af',
 'src/trading_runtime/numbered_fixed_strategy.py': '3292fce6d35bcf81fc3ce284d47ce133b49dbc528c902178f15f40d27cc3bc46',
 'src/trading_runtime/order_management.py': 'c0b3fb8f09a5c554e937ab0fec1f419e290e0398755c1ef71e4d363958f0343c',
 'src/trading_runtime/portfolio.py': 'd14c31fd8d4c392f2f98c748b401a0b50042524a4d0b56a586b059e19056bf8f',
 'src/trading_runtime/portfolio_config.py': '370094eb372aea53b97c0a5185fc95421065d0ffc9c2a6481496e006e1c60688',
 'src/trading_runtime/runtime.py': '57af631c859aa491b604830bb552f47abdc3e50e8a94d2b7fbeec234133fa465',
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
 'src/trading_runtime/strategy_registry.py': '708960fc0d38a859ae97d58f7feac74aa8c8087671b826d3acae0eb4d15a0a9f',
 'src/trading_runtime/strategy_sixty_five_contract.py': '491691d4ffc7e750cce203e19f2359a52957e13db08958255de606f8e8ce48b1',
 'src/trading_runtime/strategy_sixty_five_release.py': '4c5015d7b3923272affe263eee8c8c2d18cf452fb7d229376203480f4078b256',
 'src/backend/backtest_market_data.py': 'ad4135f16a0979b5af821b0c00c294511635b3b84b37b90f7bdff79e503cf9d3',
 'src/backend/backtest_fixed_v4_certification.py': {'certify_numbered_fixed_v4_projection': '7354042a5ae61438cbd56a8db4bd10aa8fff9453d22a029f8bb8ae3a3a1bc52d'},
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
 'scripts/clickhouse/provision_backtest_v4_runner.py': 'a97b2323c1104b0c81396cc9ad5999d5962ce44bffc8617916ff9f67993fe056',
 'scripts/clickhouse/provision_fixed_backtest_v3_principals.py': '77df1f30c5fb6fb5700f2b8843a2d71a0f801beb27026371678666e5d228a6ee',
 'scripts/clickhouse/provision_trading_journal.py': 'fb0a05f5a3098bcd28482db4b7f34dc3a47e3ac436bca0b0ca747262dd1e507e',
 'src/backend/backtest_strategy_sixty_five_certification.py': {'certify_strategy_sixty_five_source': 'f86857ac1e12e292e5bba74168e383b2abf9f1ee21aa82bd451c699e05f7fa9a'},
 'src/trading_runtime/clickhouse_transport.py': '108e4e9a4e757ed792ab01f8d7faefdc035b2da398cfcbdd8dc3917c20cbb5df',
 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
 'research/mlops/env.py': '856dc976500cfd9efc0648b22a28665951f8bdeb81328c45a47f5dc586b5895b'}


def certify_strategy_sixty_five_source(*, source_overrides=None):
    """Verify ordered declarations, their anchor and fresh native source bytes."""
    self_relative = 'src/backend/backtest_strategy_sixty_five_certification.py'
    symbol_leaves = {
        'src/backend/backtest_fixed_v4_certification.py': ('certify_numbered_fixed_v4_projection',),
        'src/backend/backtest_strategy_sixty_five_certification.py': ('certify_strategy_sixty_five_source',),
    }
    metadata_anchor = 'a70bae63936da88c508e90397eb9c2a463035ee2ccd9afa03fe0809335774fd5'
    overrides = {} if source_overrides is None else source_overrides
    if type(overrides) is not dict or set(overrides) - set(REQUIRED_SOURCE_FILES):
        raise ValueError('Strategy65 source override is outside complete authority')
    if (type(REQUIRED_SOURCE_FILES) is not tuple
            or any(type(path) is not str for path in REQUIRED_SOURCE_FILES)
            or len(set(REQUIRED_SOURCE_FILES)) != len(REQUIRED_SOURCE_FILES)
            or type(STRATEGY65_SOURCE_AST) is not dict
            or tuple(STRATEGY65_SOURCE_AST) != REQUIRED_SOURCE_FILES):
        raise ValueError('Strategy65 complete source review is not sealed')
    for relative, expected in STRATEGY65_SOURCE_AST.items():
        if relative in symbol_leaves:
            if type(expected) is not dict or tuple(expected) != symbol_leaves[relative]:
                raise ValueError('Strategy65 source selector shape changed: '+relative)
            values = tuple(expected.values())
        else:
            if type(expected) is not str:
                raise ValueError('Strategy65 source digest shape changed: '+relative)
            values = (expected,)
        if any(type(value) is not str or len(value) != 64
               or any(c not in '0123456789abcdef' for c in value) for value in values):
            raise ValueError('Strategy65 source digest is malformed: '+relative)
    metadata = dict(required=REQUIRED_SOURCE_FILES,
        sources={path:value for path,value in STRATEGY65_SOURCE_AST.items() if path != self_relative},
        symbols=symbol_leaves)
    if sha256(json.dumps(metadata,sort_keys=True,separators=(',', ':')).encode()).hexdigest() != metadata_anchor:
        raise ValueError('Strategy65 source metadata anchor changed')
    root = Path(__file__).parents[2]
    observed = []
    for relative in REQUIRED_SOURCE_FILES:
        expected = STRATEGY65_SOURCE_AST[relative]
        source = Path(overrides.get(relative, root/relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Strategy65 source cannot be parsed: '+relative) from exc
        if relative in symbol_leaves:
            for symbol, digest in expected.items():
                nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == symbol]
                if len(nodes) != 1 or sha256(ast.unparse(nodes[0]).encode()).hexdigest() != digest:
                    raise ValueError('Strategy65 pinned source changed: '+relative+':'+symbol)
        elif sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy65 pinned source changed: '+relative)
        if relative == self_relative:
            envelope = tree.body
            imports = ('from hashlib import sha256', 'import json',
                       'from pathlib import Path', 'import ast')
            if (len(envelope) != 8
                    or not isinstance(envelope[0],ast.Expr)
                    or not isinstance(envelope[0].value,ast.Constant)
                    or envelope[0].value.value != 'Closed selected waiting baseline source authority; publication is separate.'
                    or tuple(ast.unparse(node) for node in envelope[1:5]) != imports
                    or any(not isinstance(node,ast.Assign) or len(node.targets) != 1
                           or not isinstance(node.targets[0],ast.Name)
                           for node in envelope[5:7])
                    or tuple(node.targets[0].id for node in envelope[5:7])
                       != ('REQUIRED_SOURCE_FILES','STRATEGY65_SOURCE_AST')
                    or not isinstance(envelope[7],ast.FunctionDef)
                    or envelope[7].name != 'certify_strategy_sixty_five_source'):
                raise ValueError('Strategy65 own source module envelope changed')
            declarations = {}
            for node in tree.body:
                if (isinstance(node,ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0],ast.Name)
                        and node.targets[0].id in ('REQUIRED_SOURCE_FILES','STRATEGY65_SOURCE_AST')):
                    name = node.targets[0].id
                    if name in declarations:
                        raise ValueError('Strategy65 fresh source declaration duplicated')
                    declarations[name] = ast.literal_eval(node.value)
            if (set(declarations) != {'REQUIRED_SOURCE_FILES','STRATEGY65_SOURCE_AST'}
                    or declarations['REQUIRED_SOURCE_FILES'] != REQUIRED_SOURCE_FILES
                    or declarations['STRATEGY65_SOURCE_AST'] != STRATEGY65_SOURCE_AST):
                raise ValueError('Strategy65 fresh source declarations differ')
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed,separators=(',', ':')).encode()).hexdigest()
