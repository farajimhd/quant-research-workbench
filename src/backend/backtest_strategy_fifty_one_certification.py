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
 'src/trading_runtime/strategy_registry.py': '4d08aee98475473a2772c2619cf69b7232e22a7dbb2373f7a50ead40ab8699d8',
 'src/trading_runtime/numbered_fixed_strategy.py': '0f6a62a9c7096f278caacc6861fff149978497779e4f72f0140d8466ee7389ea',
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
 'src/backend/backtest_ladder_source_authority.py': 'c2b068dd9dd31ddf170c0a08d143864b5dbd212dc021f261448c8c54aad27a71',
 'src/backend/backtest_market_data.py': 'ad4135f16a0979b5af821b0c00c294511635b3b84b37b90f7bdff79e503cf9d3',
 'src/data_provider/calendar.py': '82706e238be3f8e13dae1177928c02a09288fed18540932dfedc11e794ca4c0e',
 'src/trading_runtime/arte_backtest_snapshot_anchor.py': '234893befe310819b6679a5541836789dc00d23b7ad9cdc10076ca3395c01087',
 'scripts/clickhouse/report_strategy_one_trades.py': 'c8c52c781468a4b442d0df933f706b0d0e400cbe70537184ab2a2b4bb7723fed',
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
 'src/backend/backtest_strategy_one_configuration.py': 'e13e33f96c71278223ab8babc839052f3659f2e3bbccbd8beb79ac61deef24ca',
 'src/backend/backtest_strategy_one_execution.py': '6891230fd947b41e708aa458ce6563202135f867dc12a572e0a9f5f40f01b83c',
 'src/backend/backtest_strategy_one_coordinator.py': '79063bebc5d92af4e1704c6944fbbe2b81a2ba3780d8dc8de37dc21688bacfe1',
 'src/backend/backtest_journal_memory.py': 'da93c31c96151883cd04196e956ab28319ac0943c2353b5b717719380d0606a2',
 'src/backend/backtest_typed_projection.py': '1fb9b10d98019e8a2291a8ef5a759fc4aee58995da63dc64c5657878f4a0d759',
 'src/backend/backtest_typed_publisher.py': '7134af423e8b564586532df8993c731761541c5d31883dd35c88d2525f6fcf04',
 'src/backend/backtest_v4_saved_review.py': '0645e6e3dacb127c3c50e8afc0bae4a867916822f3fd9310b60d8ad6a1c85928',
 'src/backend/replay_run_service.py': '4291f16739779c5bd880a1049bf25f736bb32799b321770b6526e41dbe6c7035',
 'src/trading_runtime/runtime.py': '09e06f9cfa3bc9d6fbe3e590b04fe2787947a3e6be835839e3d1b1d10c38831f',
 'src/trading_runtime/arte_journal_writer.py': 'c9fd82b8dd967ee08771491b4dad695a82db9dc010326227a3a6e528f837c1c4',
 'src/trading_runtime/arte_journal_commit_v4.py': '658edbb634d13c32bdf976d7098ba5f503f3d691579c820c29a64b74e66752c8',
 'src/trading_runtime/arte_oms_projection.py': '9037a91b71561a7b521d56c6428391c88add5505064d65bd6998a06f26e29c12',
 'pipelines/strategy_one/configuration_publisher.py': '7d18b48bcab46736081384bf1ce994f4e9edda501c22254e4c12d755954f7874',
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
