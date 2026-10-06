"""Full B source seal; populated only after all coordinated lanes are reviewed."""
import ast
from hashlib import sha256
import json
from pathlib import Path

REQUIRED_SOURCE_FILES = ('pipelines/strategy_one/configuration_publisher.py',
 'pipelines/strategy_one/strategy_fifty_one_configuration.py',
 'scripts/clickhouse/provision_backtest_v4_ladder_runner.py',
 'scripts/clickhouse/publish_strategy_fifty_one_configuration.py',
 'scripts/clickhouse/report_strategy_one_trades.py',
 'src/backend/backtest_declared_initial_momentum.py',
 'src/backend/backtest_declared_ladder_plan.py',
 'src/backend/backtest_declared_ladder_seed.py',
 'src/backend/backtest_journal_memory.py',
 'src/backend/backtest_ladder_coordinator.py',
 'src/backend/backtest_ladder_entry_authority.py',
 'src/backend/backtest_ladder_source_authority.py',
 'src/backend/backtest_market_data.py',
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
 'src/trading_runtime/numbered_fixed_strategy.py',
 'src/trading_runtime/runtime.py',
 'src/trading_runtime/session_acquisition_admission.py',
 'src/trading_runtime/squeeze_ladder_admission.py',
 'src/trading_runtime/squeeze_ladder_automatic.py',
 'src/trading_runtime/squeeze_ladder_columnar.py',
 'src/trading_runtime/squeeze_ladder_cross.py',
 'src/trading_runtime/squeeze_ladder_lots.py',
 'src/trading_runtime/squeeze_ladder_protection.py',
 'src/trading_runtime/squeeze_ladder_setup.py',
 'src/trading_runtime/strategy_fifty_one_contract.py',
 'src/trading_runtime/strategy_fifty_one_release.py',
 'src/trading_runtime/strategy_registry.py',
 'src/trading_runtime/squeeze_ladder_geometry.py')

# Reviewed complete native execution, independent cold proof and release routes.
STRATEGY51_SOURCE_AST = {'src/trading_runtime/strategy_fifty_one_contract.py': 'c26c649bb6853c6d9d8b3f92a14fda1dae112330f716c4b0283aae25ffce3604', 'src/trading_runtime/strategy_fifty_one_release.py': '80243bf85739368b041f218041ea44796fd5994e0feedc9a202610a356f77499', 'src/trading_runtime/strategy_registry.py': '708960fc0d38a859ae97d58f7feac74aa8c8087671b826d3acae0eb4d15a0a9f', 'src/trading_runtime/numbered_fixed_strategy.py': '3292fce6d35bcf81fc3ce284d47ce133b49dbc528c902178f15f40d27cc3bc46', 'src/trading_runtime/squeeze_ladder_columnar.py': '89ad45037b4aadf78a19420a7c6eccdbc2633d76b034070326ae980c34b90427', 'src/trading_runtime/squeeze_ladder_cross.py': 'd47b50b0d97da17a3840a6fbf5e394a5dc1e2041a9e0c38693888eb65251f8c0', 'src/trading_runtime/squeeze_ladder_setup.py': 'f8a63bbbb9b9312c3eb88eaaddd9e74a5fb053f9d5bdee7260fce20b9a52fe9a', 'src/trading_runtime/squeeze_ladder_protection.py': '31b1a3c9995f80d6dc1d314a8de26a909917bf892d9eafacc62e3ae27ac8a374', 'src/trading_runtime/session_acquisition_admission.py': '642c51e9fedd015db64aa534ddd41d92dec456fa40bb82aeb01c9965afd4bebb', 'src/trading_runtime/squeeze_ladder_admission.py': 'b159550fded242bc676242083f882060cd4d862d749fab0e99d682d44b112a0a', 'src/trading_runtime/arte_squeeze_ladder_schema.py': '894849755a2a22935b7d9c052dfc7b2eace9f72c73c0432f21c94880d5752c66', 'src/trading_runtime/squeeze_ladder_lots.py': '0b31d6955210ec0e28f6411f2b921e465046fbda21b101da0cca12f02efc61b2', 'src/trading_runtime/squeeze_ladder_automatic.py': '0474d98c356ea6999afe132e3295fa6e703d87d5227f7fe0186d6213e9b5b91d', 'src/trading_runtime/automatic_ladder_transport.py': 'e2b62165a417fe3465af98353c663e1417c3a7723563c45c3e63941ae722629a', 'src/backend/backtest_ladder_entry_authority.py': '9971905edc1073434fc074dae9afdba54cc86b04793c019366c9bd9830086b21', 'src/backend/backtest_ladder_source_authority.py': '02e9ad68a84b32da0770ba0c68d1a4fca0cc3747e78537a48da2aeb7f5d75c52', 'src/backend/backtest_market_data.py': 'ad4135f16a0979b5af821b0c00c294511635b3b84b37b90f7bdff79e503cf9d3', 'src/data_provider/calendar.py': '82706e238be3f8e13dae1177928c02a09288fed18540932dfedc11e794ca4c0e', 'src/trading_runtime/arte_backtest_snapshot_anchor.py': '234893befe310819b6679a5541836789dc00d23b7ad9cdc10076ca3395c01087', 'scripts/clickhouse/report_strategy_one_trades.py': '04d1b0a3c0a9bd58785d8753c87b4207f225fc1c0636081a22ad0662a5a925f4', 'src/backend/backtest_ladder_coordinator.py': 'a74dbe92b1a4dd7faf140779136e8e0da983051f11a0714bb4ac164aafc3e8e1', 'src/backend/backtest_declared_ladder_plan.py': 'a6db11ddef4ccfe9707fc60bf0e998c106eda12c84634b8caf49b2ff4e1373cb', 'src/backend/backtest_declared_ladder_seed.py': '01637a936e178665f35725ea18e6d6ef132ece88a3b87a7a578d32c2010f10d8', 'src/backend/backtest_squeeze_ladder_loader.py': 'c905275276fe6e3b85814486c5c5e58df7b8e612a9595178b4a829753713ffd3', 'src/backend/backtest_squeeze_ladder_entry.py': 'ceb72094e5eaf30e08a9334dc98ff82bec6d3ca3970438d756a038cb16781d3d', 'src/backend/backtest_squeeze_ladder_setup.py': 'f86fab2f6a863bed811b9e1785efd17f55447f6ce84da8ad2adc9654ee1fde41', 'src/backend/backtest_squeeze_ladder_journal_admission.py': 'c01a7a5b87f33b2b08847bf18612eec51b69ad06d4ad692f1847450b3c8ba55b', 'src/backend/backtest_squeeze_ladder_readback.py': 'ebe50e9a4fa2bb1f620ef4067f8cb5152f2b1802d17388c1fd70a6857b26d066', 'src/backend/backtest_squeeze_ladder_admission.py': '7126dd2ba2046ea88c3917ed33ee4b7007a309e16aab611f86c323d407565b46', 'src/backend/backtest_squeeze_ladder_evidence.py': '5435128884fdadb4490d876396199ad5696a65833307f8b6eae6284c23032b4e', 'src/backend/backtest_strategy_one_configuration.py': '7fb2771427ab2bb7e629d8d20b7208c1af7068689fb92eb16fed5a6907bd0ccd', 'src/backend/backtest_strategy_one_execution.py': '637da264cea91bcf316473704933e6b8a0932b43509a6d5a3ffb9809451a9a93', 'src/backend/backtest_strategy_one_coordinator.py': '7d40c9a3d75872e1ff263729eb578cf9860e913bae3863ec8889ca999e5df334', 'src/backend/backtest_journal_memory.py': '6c31e1a22ea4ae131363e4a7b630f0edeb0ab14bb0c0c641cd4ecb8175ba2864', 'src/backend/backtest_typed_projection.py': '942a03a57b59c9371c1fb2f62310ab31706d56f1e2a5f2326ba3efb745e9908c', 'src/backend/backtest_typed_publisher.py': '24e615607412b1a477f405a4188c24db67b69031a74965de0fbc189ee369c218', 'src/backend/backtest_v4_saved_review.py': 'e75fcb1c1fbb4689f5144d5964248b8e2e2402428048d5afb6177d986620664e', 'src/backend/replay_run_service.py': '1871d07a8d40f07def8accc0811ca228320d49940a9a122f8f71c496e5831440', 'src/trading_runtime/runtime.py': '57af631c859aa491b604830bb552f47abdc3e50e8a94d2b7fbeec234133fa465', 'src/trading_runtime/arte_journal_writer.py': '3040cb3e96f6fc0448ce7ab039d1dd08fbaa37126777e9954c4a5b33aac13a31', 'src/trading_runtime/arte_journal_commit_v4.py': 'b554846e3a213c17818c6e89cfb3baee1f0738834caf3dc0d0d3b526b16d0e22', 'src/trading_runtime/arte_oms_projection.py': 'a4514545219759a1ae0c79465531e548ec2e34584c61d1fb7abc09da130893f9', 'pipelines/strategy_one/configuration_publisher.py': 'd6eedccd686e0d26b39153cf8cb07d753f9fae1f77818cf367b0e30ef6570de1', 'pipelines/strategy_one/strategy_fifty_one_configuration.py': '6bd547973eb878042202f1aca3454dbee3f12617c74273cd5773d40f7c302c43', 'scripts/clickhouse/publish_strategy_fifty_one_configuration.py': '7f171365e2a82b010091479c74f75f1048239d932eb5f6144ff6f434ab3eec68', 'scripts/clickhouse/provision_backtest_v4_ladder_runner.py': 'ef34b7dfc73d05919f04b55d616f4950b7f6f3547a88e3a2d342ed2e6ab57f8e', 'src/trading_runtime/entry_momentum_growth.py': '8acec288e9cf7d1c62bf8cb2fa386f82d5db69a63fc9306b90168f8cee3c0b5c', 'src/backend/backtest_declared_initial_momentum.py': '786a12c62a157c092652c8cd7d695fe4f5e8ebfb315c9b8dbab56580fb0a4b81', 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995', 'src/trading_runtime/squeeze_ladder_geometry.py': '34658cac7dba475b19ca3963bbcad1397a88f5f86e4133664947c9e656671e0d'}


def certify_strategy_fifty_one_source(*, source_overrides=None):
    overrides = source_overrides or {}
    if set(STRATEGY51_SOURCE_AST) != set(REQUIRED_SOURCE_FILES):
        raise ValueError('Strategy51 complete source review is not sealed')
    if set(overrides) - set(STRATEGY51_SOURCE_AST):
        raise ValueError('Strategy51 source override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in STRATEGY51_SOURCE_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Strategy51 source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy51 pinned source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
