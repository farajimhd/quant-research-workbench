"""Full B source seal; populated only after all coordinated lanes are reviewed."""
import ast
from hashlib import sha256
import json
from pathlib import Path

REQUIRED_SOURCE_FILES = ('pipelines/strategy_one/configuration_publisher.py',
 'pipelines/strategy_one/strategy_forty_nine_configuration.py',
 'scripts/clickhouse/provision_backtest_v4_ladder_runner.py',
 'scripts/clickhouse/publish_strategy_forty_nine_configuration.py',
 'scripts/clickhouse/report_strategy_one_trades.py',
 'src/backend/backtest_declared_initial_momentum.py',
 'src/backend/backtest_declared_ladder_plan.py',
 'src/backend/backtest_declared_ladder_seed.py',
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
 'src/trading_runtime/strategy_forty_nine_contract.py',
 'src/trading_runtime/strategy_forty_nine_release.py',
 'src/trading_runtime/strategy_registry.py')

# Reviewed complete native execution, independent cold proof and release routes.
STRATEGY49_SOURCE_AST = {'src/trading_runtime/strategy_forty_nine_contract.py': '6021520d67cdd2bbf03a0296a205a8f00480092d4fcc4d1dbcdcac9dea0ff1ac', 'src/trading_runtime/strategy_forty_nine_release.py': 'b42fb373e595c66aa82daa5f2b70ca1b68038bca2a18a7dc3e67a6a45904b117', 'src/trading_runtime/strategy_registry.py': 'ebc3dd4112a8c37b1219f5e5d1207cb12a28a84d9c527236a985e43c480ae919', 'src/trading_runtime/numbered_fixed_strategy.py': '8e545e625e8640e7ad4782394bdffdfbd9ab26789c8a7930a939457df93eb542', 'src/trading_runtime/squeeze_ladder_columnar.py': '89ad45037b4aadf78a19420a7c6eccdbc2633d76b034070326ae980c34b90427', 'src/trading_runtime/squeeze_ladder_cross.py': 'd47b50b0d97da17a3840a6fbf5e394a5dc1e2041a9e0c38693888eb65251f8c0', 'src/trading_runtime/squeeze_ladder_setup.py': 'f8a63bbbb9b9312c3eb88eaaddd9e74a5fb053f9d5bdee7260fce20b9a52fe9a', 'src/trading_runtime/squeeze_ladder_protection.py': '31b1a3c9995f80d6dc1d314a8de26a909917bf892d9eafacc62e3ae27ac8a374', 'src/trading_runtime/session_acquisition_admission.py': '642c51e9fedd015db64aa534ddd41d92dec456fa40bb82aeb01c9965afd4bebb', 'src/trading_runtime/squeeze_ladder_admission.py': 'b159550fded242bc676242083f882060cd4d862d749fab0e99d682d44b112a0a', 'src/trading_runtime/arte_squeeze_ladder_schema.py': '7684f07aa3dd3be180d3105607782976034df958c0fbe550feba778c39369154', 'src/trading_runtime/squeeze_ladder_lots.py': '0b31d6955210ec0e28f6411f2b921e465046fbda21b101da0cca12f02efc61b2', 'src/trading_runtime/squeeze_ladder_automatic.py': '820e57f05b18e99f591c7d2de6db2e1db1db89f3bbb29d948b902c771c2758ae', 'src/trading_runtime/automatic_ladder_transport.py': 'f500de7c93c19127ed8749f7b25510cb88fe819749074866c47bb8f84e2ddb1d', 'src/backend/backtest_ladder_entry_authority.py': '5f92ae6f64b3820cdeedd63fd75089aa03ce725d65e7995c10739639401ed3b0', 'src/backend/backtest_ladder_source_authority.py': 'c2b068dd9dd31ddf170c0a08d143864b5dbd212dc021f261448c8c54aad27a71', 'src/data_provider/calendar.py': '82706e238be3f8e13dae1177928c02a09288fed18540932dfedc11e794ca4c0e', 'src/trading_runtime/arte_backtest_snapshot_anchor.py': '234893befe310819b6679a5541836789dc00d23b7ad9cdc10076ca3395c01087', 'scripts/clickhouse/report_strategy_one_trades.py': 'ea68a1fc4d3c6ab8c1bd41f6d80882d4c52b06d3c8bd8c76f93afeceb9bd60c5', 'src/backend/backtest_ladder_coordinator.py': '35db55511be942be9d84f1db7d52cb4278a4ae76f00aae4317df04000a47136a', 'src/backend/backtest_declared_ladder_plan.py': 'a6db11ddef4ccfe9707fc60bf0e998c106eda12c84634b8caf49b2ff4e1373cb', 'src/backend/backtest_declared_ladder_seed.py': '01637a936e178665f35725ea18e6d6ef132ece88a3b87a7a578d32c2010f10d8', 'src/backend/backtest_squeeze_ladder_loader.py': 'c905275276fe6e3b85814486c5c5e58df7b8e612a9595178b4a829753713ffd3', 'src/backend/backtest_squeeze_ladder_entry.py': 'ceb72094e5eaf30e08a9334dc98ff82bec6d3ca3970438d756a038cb16781d3d', 'src/backend/backtest_squeeze_ladder_setup.py': '8e41ec144430b6705286a8b0520343c723611f810026369c34af48a8d0779a17', 'src/backend/backtest_squeeze_ladder_journal_admission.py': '4d5bcf1291f0b210048140f69865c5d943980d69f5ace0ef9e38828198a1f612', 'src/backend/backtest_squeeze_ladder_readback.py': '252df3d135235b4afafcd1b5005f590746485969ed498ee25c8e05752b8aa73e', 'src/backend/backtest_squeeze_ladder_admission.py': '7126dd2ba2046ea88c3917ed33ee4b7007a309e16aab611f86c323d407565b46', 'src/backend/backtest_squeeze_ladder_evidence.py': '4f0963b9433262bbbba2fce52a3d361d0cc32ee932cffc282438d1cccfc080ad', 'src/backend/backtest_strategy_one_configuration.py': '753a33078263f96c709151a26e3938bc401c6f07f042ef2d5d18067d653e4c13', 'src/backend/backtest_strategy_one_execution.py': '637da264cea91bcf316473704933e6b8a0932b43509a6d5a3ffb9809451a9a93', 'src/backend/backtest_strategy_one_coordinator.py': '7d40c9a3d75872e1ff263729eb578cf9860e913bae3863ec8889ca999e5df334', 'src/backend/backtest_journal_memory.py': '6c31e1a22ea4ae131363e4a7b630f0edeb0ab14bb0c0c641cd4ecb8175ba2864', 'src/backend/backtest_typed_projection.py': '25745f25324d9c144cae387522c4dd338c5feb2192dccf62b12dcbb4b36a1f1e', 'src/backend/backtest_typed_publisher.py': '24e615607412b1a477f405a4188c24db67b69031a74965de0fbc189ee369c218', 'src/backend/backtest_v4_saved_review.py': 'a4df8a5dbe2257a2b0a7bb054dc2cf714204fe1e26c71334dc57b2415c8db2cd', 'src/backend/replay_run_service.py': '42bec353230c81c1a0e8367cd907785486ff7616277b8d5f0752ad91a12ede07', 'src/trading_runtime/runtime.py': 'bf4c367984ba72ed39019b9721292d09dbba6e1850198fca18f290dd3a382bea', 'src/trading_runtime/arte_journal_writer.py': '109347c5eb6d2b70747e0f7daf4a919cbd5c78b236063299caa401273b16e6f6', 'src/trading_runtime/arte_journal_commit_v4.py': '11a1fb017d9a6865a97daf4d8f47fdea8407af435ee717cfd124cee2f29936c3', 'src/trading_runtime/arte_oms_projection.py': 'a3c6169b2b158b69524493e7a9b538473362e7b76c9a0e13a9ad39ca933b6e73', 'pipelines/strategy_one/configuration_publisher.py': '72e52c4bbca9c2e97183ffd494089d3f4c09fe1f7da0c7de1d7f0aa3db063941', 'pipelines/strategy_one/strategy_forty_nine_configuration.py': 'aba600a9fb5d7940ac5442c954b92ce611f99117177d018295b7e89658183fa9', 'scripts/clickhouse/publish_strategy_forty_nine_configuration.py': '91f90ccd0ddf3695d8f2901b9c9351da922d7837dca97bf90a141c9448362072', 'scripts/clickhouse/provision_backtest_v4_ladder_runner.py': 'ef34b7dfc73d05919f04b55d616f4950b7f6f3547a88e3a2d342ed2e6ab57f8e', 'src/trading_runtime/entry_momentum_growth.py': '8acec288e9cf7d1c62bf8cb2fa386f82d5db69a63fc9306b90168f8cee3c0b5c', 'src/backend/backtest_declared_initial_momentum.py': '786a12c62a157c092652c8cd7d695fe4f5e8ebfb315c9b8dbab56580fb0a4b81', 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995'}


def certify_strategy_forty_nine_source(*, source_overrides=None):
    overrides = source_overrides or {}
    if set(STRATEGY49_SOURCE_AST) != set(REQUIRED_SOURCE_FILES):
        raise ValueError('Strategy49 complete source review is not sealed')
    if set(overrides) - set(STRATEGY49_SOURCE_AST):
        raise ValueError('Strategy49 source override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in STRATEGY49_SOURCE_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Strategy49 source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy49 pinned source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
