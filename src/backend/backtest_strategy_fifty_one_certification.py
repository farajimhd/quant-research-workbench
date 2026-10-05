"""Full B source seal; populated only after all coordinated lanes are reviewed."""
import ast
from hashlib import sha256
import json
from pathlib import Path

REQUIRED_SOURCE_FILES = (
    'src/trading_runtime/strategy_fifty_one_contract.py',
    'src/trading_runtime/strategy_fifty_one_release.py',
    'src/trading_runtime/strategy_registry.py',
    'src/trading_runtime/numbered_fixed_strategy.py',
    'src/trading_runtime/squeeze_ladder_columnar.py',
    'src/trading_runtime/squeeze_ladder_cross.py',
    'src/trading_runtime/squeeze_ladder_setup.py',
    'src/trading_runtime/squeeze_ladder_protection.py',
    'src/trading_runtime/session_acquisition_admission.py',
    'src/trading_runtime/squeeze_ladder_admission.py',
    'src/trading_runtime/arte_squeeze_ladder_schema.py',
    'src/trading_runtime/squeeze_ladder_lots.py',
    'src/trading_runtime/squeeze_ladder_automatic.py',
    'src/trading_runtime/automatic_ladder_transport.py',
    'src/backend/backtest_ladder_entry_authority.py',
    'src/backend/backtest_ladder_source_authority.py',
    'src/backend/backtest_market_data.py',
    'src/data_provider/calendar.py',
    'src/trading_runtime/arte_backtest_snapshot_anchor.py',
    'scripts/clickhouse/report_strategy_one_trades.py',
    'src/backend/backtest_ladder_coordinator.py',
    'src/backend/backtest_declared_ladder_plan.py',
    'src/backend/backtest_declared_ladder_seed.py',
    'src/backend/backtest_squeeze_ladder_loader.py',
    'src/backend/backtest_squeeze_ladder_entry.py',
    'src/backend/backtest_squeeze_ladder_setup.py',
    'src/backend/backtest_squeeze_ladder_journal_admission.py',
    'src/backend/backtest_squeeze_ladder_readback.py',
    'src/backend/backtest_squeeze_ladder_admission.py',
    'src/backend/backtest_squeeze_ladder_evidence.py',
    'src/backend/backtest_strategy_one_configuration.py',
    'src/backend/backtest_strategy_one_execution.py',
    'src/backend/backtest_strategy_one_coordinator.py',
    'src/backend/backtest_journal_memory.py',
    'src/backend/backtest_typed_projection.py',
    'src/backend/backtest_typed_publisher.py',
    'src/backend/backtest_v4_saved_review.py',
    'src/backend/replay_run_service.py',
    'src/trading_runtime/runtime.py',
    'src/trading_runtime/arte_journal_writer.py',
    'src/trading_runtime/arte_journal_commit_v4.py',
    'src/trading_runtime/arte_oms_projection.py',
    'pipelines/strategy_one/configuration_publisher.py',
    'pipelines/strategy_one/strategy_fifty_one_configuration.py',
    'scripts/clickhouse/publish_strategy_fifty_one_configuration.py',
    'scripts/clickhouse/provision_backtest_v4_ladder_runner.py',
)

# Reviewed complete native execution, independent cold proof and release routes.
STRATEGY51_SOURCE_AST = {'src/trading_runtime/strategy_fifty_one_contract.py': 'c26c649bb6853c6d9d8b3f92a14fda1dae112330f716c4b0283aae25ffce3604',
 'src/trading_runtime/strategy_fifty_one_release.py': '80243bf85739368b041f218041ea44796fd5994e0feedc9a202610a356f77499',
 'src/trading_runtime/strategy_registry.py': '5d67e5a268fb2b07824c733033a9907237ba05dc962289cc51f3262333dc1efe',
 'src/trading_runtime/numbered_fixed_strategy.py': 'e77afb232629caaf7cd736f3e42ffda9e71690003fd3943b690b142742c55d6f',
 'src/trading_runtime/squeeze_ladder_columnar.py': '89ad45037b4aadf78a19420a7c6eccdbc2633d76b034070326ae980c34b90427',
 'src/trading_runtime/squeeze_ladder_cross.py': 'd47b50b0d97da17a3840a6fbf5e394a5dc1e2041a9e0c38693888eb65251f8c0',
 'src/trading_runtime/squeeze_ladder_setup.py': 'f8a63bbbb9b9312c3eb88eaaddd9e74a5fb053f9d5bdee7260fce20b9a52fe9a',
 'src/trading_runtime/squeeze_ladder_protection.py': '31b1a3c9995f80d6dc1d314a8de26a909917bf892d9eafacc62e3ae27ac8a374',
 'src/trading_runtime/session_acquisition_admission.py': '642c51e9fedd015db64aa534ddd41d92dec456fa40bb82aeb01c9965afd4bebb',
 'src/trading_runtime/squeeze_ladder_admission.py': 'b159550fded242bc676242083f882060cd4d862d749fab0e99d682d44b112a0a',
 'src/trading_runtime/arte_squeeze_ladder_schema.py': '7684f07aa3dd3be180d3105607782976034df958c0fbe550feba778c39369154',
 'src/trading_runtime/squeeze_ladder_lots.py': '0b31d6955210ec0e28f6411f2b921e465046fbda21b101da0cca12f02efc61b2',
 'src/trading_runtime/squeeze_ladder_automatic.py': '820e57f05b18e99f591c7d2de6db2e1db1db89f3bbb29d948b902c771c2758ae',
 'src/trading_runtime/automatic_ladder_transport.py': 'f500de7c93c19127ed8749f7b25510cb88fe819749074866c47bb8f84e2ddb1d',
 'src/backend/backtest_ladder_entry_authority.py': '5f92ae6f64b3820cdeedd63fd75089aa03ce725d65e7995c10739639401ed3b0',
 'src/backend/backtest_ladder_source_authority.py': '5477f63cf796f4e7cd277d5d0bf326efc1111f852f1817c499db89a493495eb7',
 'src/backend/backtest_market_data.py': 'ad4135f16a0979b5af821b0c00c294511635b3b84b37b90f7bdff79e503cf9d3',
 'src/data_provider/calendar.py': '82706e238be3f8e13dae1177928c02a09288fed18540932dfedc11e794ca4c0e',
 'src/trading_runtime/arte_backtest_snapshot_anchor.py': '234893befe310819b6679a5541836789dc00d23b7ad9cdc10076ca3395c01087',
 'scripts/clickhouse/report_strategy_one_trades.py': 'ee29f3c65ff43877f350922d69c9e2f779b6da3cd16a5fd6f02c5636fc950b5d',
 'src/backend/backtest_ladder_coordinator.py': '35db55511be942be9d84f1db7d52cb4278a4ae76f00aae4317df04000a47136a',
 'src/backend/backtest_declared_ladder_plan.py': 'a6db11ddef4ccfe9707fc60bf0e998c106eda12c84634b8caf49b2ff4e1373cb',
 'src/backend/backtest_declared_ladder_seed.py': '01637a936e178665f35725ea18e6d6ef132ece88a3b87a7a578d32c2010f10d8',
 'src/backend/backtest_squeeze_ladder_loader.py': 'c905275276fe6e3b85814486c5c5e58df7b8e612a9595178b4a829753713ffd3',
 'src/backend/backtest_squeeze_ladder_entry.py': 'ceb72094e5eaf30e08a9334dc98ff82bec6d3ca3970438d756a038cb16781d3d',
 'src/backend/backtest_squeeze_ladder_setup.py': '8e41ec144430b6705286a8b0520343c723611f810026369c34af48a8d0779a17',
 'src/backend/backtest_squeeze_ladder_journal_admission.py': '4d5bcf1291f0b210048140f69865c5d943980d69f5ace0ef9e38828198a1f612',
 'src/backend/backtest_squeeze_ladder_readback.py': '252df3d135235b4afafcd1b5005f590746485969ed498ee25c8e05752b8aa73e',
 'src/backend/backtest_squeeze_ladder_admission.py': '7126dd2ba2046ea88c3917ed33ee4b7007a309e16aab611f86c323d407565b46',
 'src/backend/backtest_squeeze_ladder_evidence.py': '4f0963b9433262bbbba2fce52a3d361d0cc32ee932cffc282438d1cccfc080ad',
 'src/backend/backtest_strategy_one_configuration.py': 'd5a2bc061571e09ae55ea611e7e99f5e608f9ab9dec7e350d2742d7fca3224c9',
 'src/backend/backtest_strategy_one_execution.py': '4dfbb938081cdcdd651bd2a148f73e498338df141a20f162bc5507e0e3c77852',
 'src/backend/backtest_strategy_one_coordinator.py': 'e3f87d0517f559cdf90de7407092d3aad20e0a622cb551291257dc28f791b481',
 'src/backend/backtest_journal_memory.py': '717a5e4a4f5a31806282b3f7b37ba88c04e3862cd8413d49ff0f1bf79bc1277e',
 'src/backend/backtest_typed_projection.py': '22ab5cb4116e3509b27c882a6234adb16e38e9853146014d9cb4b3eca4bdba68',
 'src/backend/backtest_typed_publisher.py': 'a211e08727e013d4429582960c2e9c60a9d82348d4bb2248aaf2a189ea864be6',
 'src/backend/backtest_v4_saved_review.py': 'a66e597fafaebc7650b811b83f06f6f2092136e41a21e560313492937c162389',
 'src/backend/replay_run_service.py': 'a98a1067c73fb30daa6c17dfb4c07aadf9fb253ad595da32af8f373173fdc215',
 'src/trading_runtime/runtime.py': 'c932991d4183d8dd42999157e8370d5c0e57ce411736e8de45ce1f64fc740a2e',
 'src/trading_runtime/arte_journal_writer.py': '6c7b13c39de95592840b2224c202bb0b00e996076612bd6514e10b6c0d93204f',
 'src/trading_runtime/arte_journal_commit_v4.py': 'c1f3fa36c9e782ba6badb204b85ffe58acdfe044e40f52228c22b2e6aef5c3eb',
 'src/trading_runtime/arte_oms_projection.py': '12d74f7a51be8ee01483ef8f0147b9d77fb878823c6fd6f3b4757c4167b99caa',
 'pipelines/strategy_one/configuration_publisher.py': '2bda267ca9ea242d6be5d30460d0d66c37b3bf6935867343a201b8f20d03f00b',
 'pipelines/strategy_one/strategy_fifty_one_configuration.py': '6bd547973eb878042202f1aca3454dbee3f12617c74273cd5773d40f7c302c43',
 'scripts/clickhouse/publish_strategy_fifty_one_configuration.py': '7f171365e2a82b010091479c74f75f1048239d932eb5f6144ff6f434ab3eec68',
 'scripts/clickhouse/provision_backtest_v4_ladder_runner.py': 'ef34b7dfc73d05919f04b55d616f4950b7f6f3547a88e3a2d342ed2e6ab57f8e'}


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
