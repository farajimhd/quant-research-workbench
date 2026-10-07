"""Full B source seal; populated only after all coordinated lanes are reviewed."""
import ast
from hashlib import sha256
import json
from pathlib import Path

REQUIRED_SOURCE_FILES = (
    'pipelines/strategy_one/configuration_publisher.py',
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
    'src/trading_runtime/strategy_registry.py',
    'src/trading_runtime/squeeze_ladder_geometry.py',
    'src/trading_runtime/confirmed_original_risk_failure.py',
    'src/trading_runtime/arte_original_risk_diagnostic_v4.py',
    'src/trading_runtime/original_risk_diagnostic_profile.py',
    'src/backend/backtest_confirmed_original_risk_source.py',
    'src/backend/backtest_market_data.py',
    'src/trading_runtime/original_risk_checkpoint.py',
    'src/trading_runtime/original_risk_pending_snapshot.py',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py',
)

# Reviewed complete native execution, independent cold proof and release routes.
STRATEGY49_SOURCE_AST = {'src/trading_runtime/strategy_forty_nine_contract.py': '6021520d67cdd2bbf03a0296a205a8f00480092d4fcc4d1dbcdcac9dea0ff1ac', 'src/trading_runtime/strategy_forty_nine_release.py': 'b42fb373e595c66aa82daa5f2b70ca1b68038bca2a18a7dc3e67a6a45904b117', 'src/trading_runtime/strategy_registry.py': '570c8d53eb1133a5b14f403b71d8804970f16c65aede3d12a8ae05ee579fbc13', 'src/trading_runtime/numbered_fixed_strategy.py': '96d118e8a2ad954cb3e79b742df4634d07bae2b81913036ae78737285bd7954d', 'src/trading_runtime/squeeze_ladder_columnar.py': '89ad45037b4aadf78a19420a7c6eccdbc2633d76b034070326ae980c34b90427', 'src/trading_runtime/squeeze_ladder_cross.py': 'd47b50b0d97da17a3840a6fbf5e394a5dc1e2041a9e0c38693888eb65251f8c0', 'src/trading_runtime/squeeze_ladder_setup.py': 'f8a63bbbb9b9312c3eb88eaaddd9e74a5fb053f9d5bdee7260fce20b9a52fe9a', 'src/trading_runtime/squeeze_ladder_protection.py': '31b1a3c9995f80d6dc1d314a8de26a909917bf892d9eafacc62e3ae27ac8a374', 'src/trading_runtime/session_acquisition_admission.py': '642c51e9fedd015db64aa534ddd41d92dec456fa40bb82aeb01c9965afd4bebb', 'src/trading_runtime/squeeze_ladder_admission.py': 'b159550fded242bc676242083f882060cd4d862d749fab0e99d682d44b112a0a', 'src/trading_runtime/arte_squeeze_ladder_schema.py': '894849755a2a22935b7d9c052dfc7b2eace9f72c73c0432f21c94880d5752c66', 'src/trading_runtime/squeeze_ladder_lots.py': '0b31d6955210ec0e28f6411f2b921e465046fbda21b101da0cca12f02efc61b2', 'src/trading_runtime/squeeze_ladder_automatic.py': '0474d98c356ea6999afe132e3295fa6e703d87d5227f7fe0186d6213e9b5b91d', 'src/trading_runtime/automatic_ladder_transport.py': 'e2b62165a417fe3465af98353c663e1417c3a7723563c45c3e63941ae722629a', 'src/backend/backtest_ladder_entry_authority.py': '9971905edc1073434fc074dae9afdba54cc86b04793c019366c9bd9830086b21', 'src/backend/backtest_ladder_source_authority.py': '02e9ad68a84b32da0770ba0c68d1a4fca0cc3747e78537a48da2aeb7f5d75c52', 'src/data_provider/calendar.py': '82706e238be3f8e13dae1177928c02a09288fed18540932dfedc11e794ca4c0e', 'src/trading_runtime/arte_backtest_snapshot_anchor.py': '234893befe310819b6679a5541836789dc00d23b7ad9cdc10076ca3395c01087', 'scripts/clickhouse/report_strategy_one_trades.py': 'ae5e51880141272adf310070c163d59c287ee90433e87ec89490414f0f581f3f', 'src/backend/backtest_ladder_coordinator.py': 'a74dbe92b1a4dd7faf140779136e8e0da983051f11a0714bb4ac164aafc3e8e1', 'src/backend/backtest_declared_ladder_plan.py': 'a6db11ddef4ccfe9707fc60bf0e998c106eda12c84634b8caf49b2ff4e1373cb', 'src/backend/backtest_declared_ladder_seed.py': '01637a936e178665f35725ea18e6d6ef132ece88a3b87a7a578d32c2010f10d8', 'src/backend/backtest_squeeze_ladder_loader.py': 'c905275276fe6e3b85814486c5c5e58df7b8e612a9595178b4a829753713ffd3', 'src/backend/backtest_squeeze_ladder_entry.py': 'ceb72094e5eaf30e08a9334dc98ff82bec6d3ca3970438d756a038cb16781d3d', 'src/backend/backtest_squeeze_ladder_setup.py': 'f86fab2f6a863bed811b9e1785efd17f55447f6ce84da8ad2adc9654ee1fde41', 'src/backend/backtest_squeeze_ladder_journal_admission.py': 'c01a7a5b87f33b2b08847bf18612eec51b69ad06d4ad692f1847450b3c8ba55b', 'src/backend/backtest_squeeze_ladder_readback.py': 'ebe50e9a4fa2bb1f620ef4067f8cb5152f2b1802d17388c1fd70a6857b26d066', 'src/backend/backtest_squeeze_ladder_admission.py': '7126dd2ba2046ea88c3917ed33ee4b7007a309e16aab611f86c323d407565b46', 'src/backend/backtest_squeeze_ladder_evidence.py': '5435128884fdadb4490d876396199ad5696a65833307f8b6eae6284c23032b4e', 'src/backend/backtest_strategy_one_configuration.py': '6a7720a1ce6223e3fff3364d95a3524442c8d37ad2dea0bff121b5eaab750f29', 'src/backend/backtest_strategy_one_execution.py': 'd9ed2de2119170cf4650b139e16004c170fd2503a482831b85f40a1177ab6f8d', 'src/backend/backtest_strategy_one_coordinator.py': '7d40c9a3d75872e1ff263729eb578cf9860e913bae3863ec8889ca999e5df334', 'src/backend/backtest_journal_memory.py': '7967a2dc2bd11edd2739caa04a8c27c8fb80f3d900da8bb642a5509deaf08f33', 'src/backend/backtest_typed_projection.py': '30e7ad90b8d41270aa80ced61ed1e9b3fdd539265ff7395b9285308237db3a9e', 'src/backend/backtest_typed_publisher.py': '0b496a1667ee617a96238773ec2cbc306b4a4f798db2fc4ea0280f7d7767661f', 'src/backend/backtest_v4_saved_review.py': 'f6a2048ea195b7a39e6694e89cb7f164e4d83826197727ee90dceefb5e852ce9', 'src/backend/replay_run_service.py': '341053ab5a8a8c0d51dfafa62893237801379896aa690b2185900d6f66dec8c1', 'src/trading_runtime/runtime.py': '6e8c043db1d62b04560fc581e46f3f7d0382c49285975263dc6cff4b40dd8dc8', 'src/trading_runtime/arte_journal_writer.py': 'faafb31844ea3c0dfd7c7d5f205552d01c3e6652179612ef57298a918f93d37c', 'src/trading_runtime/arte_journal_commit_v4.py': 'fd634b0f4f16f673f6795479fd03ef258c81b33b6e264f5395305b936c4b0a1f', 'src/trading_runtime/arte_oms_projection.py': 'e0accf89d43ab445f0d0520d4b4b811c86381b540043fc1be9ee982dd419e25f', 'pipelines/strategy_one/configuration_publisher.py': 'fe6f71767e450e7a6c8c7f59dd2fbdf309b095f6a7de9e3342f25292176b6fc6', 'pipelines/strategy_one/strategy_forty_nine_configuration.py': 'aba600a9fb5d7940ac5442c954b92ce611f99117177d018295b7e89658183fa9', 'scripts/clickhouse/publish_strategy_forty_nine_configuration.py': '91f90ccd0ddf3695d8f2901b9c9351da922d7837dca97bf90a141c9448362072', 'scripts/clickhouse/provision_backtest_v4_ladder_runner.py': 'ef34b7dfc73d05919f04b55d616f4950b7f6f3547a88e3a2d342ed2e6ab57f8e', 'src/trading_runtime/entry_momentum_growth.py': '8acec288e9cf7d1c62bf8cb2fa386f82d5db69a63fc9306b90168f8cee3c0b5c', 'src/backend/backtest_declared_initial_momentum.py': '786a12c62a157c092652c8cd7d695fe4f5e8ebfb315c9b8dbab56580fb0a4b81', 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995', 'src/trading_runtime/squeeze_ladder_geometry.py': '34658cac7dba475b19ca3963bbcad1397a88f5f86e4133664947c9e656671e0d',
    'src/trading_runtime/confirmed_original_risk_failure.py': '653f8f54f8800c9b9c8dac7804fd6ef2d1ae83a2a8785aef258f0787c1413197',
    'src/trading_runtime/arte_original_risk_diagnostic_v4.py': 'c048997055bb65060ca280d197b986994a4a4c1fee2eca883ec2c5cc9fbe5a51',
    'src/trading_runtime/original_risk_diagnostic_profile.py': '595f15675892f4b9f59cb1ee1b19536e59ab47ca563f55971b39811614a4298c',
    'src/backend/backtest_confirmed_original_risk_source.py': '0df8a2bda59e1ce95b150ca9ff43a22a2752f5a602856eaeee0cf6d866dac6f0',
    'src/backend/backtest_market_data.py': 'ad4135f16a0979b5af821b0c00c294511635b3b84b37b90f7bdff79e503cf9d3',

    'src/trading_runtime/original_risk_checkpoint.py': '9049359bdf28ddbbbfdebeaa31cf48dfe4fd035cae561869f0a1e831ed4a1cee',
    'src/trading_runtime/original_risk_pending_snapshot.py': 'bef2be65cd8fa6d02a435bc8290b3e765bb496567e3abb7b3d7fd912311239a7',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'a6011032b9690f0ddb72e8db54401e57e7850431e243023f3c3b8d90d524643c',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
}


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
