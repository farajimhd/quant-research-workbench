"""Full B source seal; populated only after all coordinated lanes are reviewed."""
import ast
from hashlib import sha256
import json
from pathlib import Path

REQUIRED_SOURCE_FILES = (
    'src/trading_runtime/strategy_forty_nine_contract.py',
    'src/trading_runtime/strategy_forty_nine_release.py',
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
    'pipelines/strategy_one/strategy_forty_nine_configuration.py',
    'scripts/clickhouse/publish_strategy_forty_nine_configuration.py',
    'scripts/clickhouse/provision_backtest_v4_ladder_runner.py',
)

# Reviewed complete native execution, independent cold proof and release routes.
STRATEGY49_SOURCE_AST = {'src/trading_runtime/strategy_forty_nine_contract.py': '6021520d67cdd2bbf03a0296a205a8f00480092d4fcc4d1dbcdcac9dea0ff1ac',
 'src/trading_runtime/strategy_forty_nine_release.py': 'b42fb373e595c66aa82daa5f2b70ca1b68038bca2a18a7dc3e67a6a45904b117',
 'src/trading_runtime/strategy_registry.py': 'c7c4bda74685a3b6197440192e868151eaf4b88d0b35af165380e85f66b21ebd',
 'src/trading_runtime/numbered_fixed_strategy.py': 'aa5b7d49f4b6a0f6a83143c63fd2875fc21bebd202815d652a9d59ed4e6671c1',
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
 'src/backend/backtest_ladder_entry_authority.py': '23f9c6e2b0227840db378cd8d117377a398ece1a78e3a0ef34246d79c0487601',
 'src/backend/backtest_ladder_source_authority.py': '9c6ec39c3c9f9e3baeda57c60da83f711eb98e45b56ba0d9ec4a8560c2e1e753',
 'src/data_provider/calendar.py': '82706e238be3f8e13dae1177928c02a09288fed18540932dfedc11e794ca4c0e',
 'src/trading_runtime/arte_backtest_snapshot_anchor.py': '234893befe310819b6679a5541836789dc00d23b7ad9cdc10076ca3395c01087',
 'scripts/clickhouse/report_strategy_one_trades.py': '525b16b9415b304d7044d32f651b8832895bc85523a6d8d1589ae8ebe5996251',
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
 'src/backend/backtest_strategy_one_configuration.py': '2803e66aaeea18c8ef6cd36d9eb7db94c8c69c8de1e867090f7d4d6477df69dd',
 'src/backend/backtest_strategy_one_execution.py': '141e77c828cb552ade7a82fb78c665739b3ca699e8bf1449f8ec642c7b82442b',
 'src/backend/backtest_strategy_one_coordinator.py': 'bcb57fe0c67bb308c5d7b00f0ba73a4e10c8532dc1096ee61be217f06e662cc3',
 'src/backend/backtest_journal_memory.py': '1562f6f509f4e2077c5b64e4ec002e04bb2323569dc9136dbde216249e28ff1b',
 'src/backend/backtest_typed_projection.py': '9c79b7428940e991bd46d675f1cb2d72ed3985a897bbedc6f640bebb9202c11d',
 'src/backend/backtest_typed_publisher.py': 'ccf5913cf4dc5e4772aab174267eb68c0e45bdcb75940f2fefbd2132e35ef932',
 'src/backend/backtest_v4_saved_review.py': '0377333e6fcdea7840b72a3e0b2fd27845abc2f9b02c1197d7749db3ac352a98',
 'src/backend/replay_run_service.py': '974f969a268e07be9aff6f973d70bebb7401ad6a5c36d3742bbb58c63254ac25',
 'src/trading_runtime/runtime.py': '8e759caac1b144be2ddbb1bb9cd42cf0371585b8cd33eec74434f03111199df2',
 'src/trading_runtime/arte_journal_writer.py': 'e85109922720b079e6b5ea8fbe8cc24b16cd733e4a12a9e0dc15ffe82e78d0f3',
 'src/trading_runtime/arte_journal_commit_v4.py': 'ab86d9ff355a01f980e8d06153cd8a0f366a560374a378bc8c06a46e6475d354',
 'src/trading_runtime/arte_oms_projection.py': 'cc5f89019c77eb279b776534dc2fd78c68aae85f232b6063901d16a011466a02',
 'pipelines/strategy_one/configuration_publisher.py': '3c03d2221a858f87e2cdaaf279668e32450a9586c68600121af74b3638fa0c13',
 'pipelines/strategy_one/strategy_forty_nine_configuration.py': 'aba600a9fb5d7940ac5442c954b92ce611f99117177d018295b7e89658183fa9',
 'scripts/clickhouse/publish_strategy_forty_nine_configuration.py': '91f90ccd0ddf3695d8f2901b9c9351da922d7837dca97bf90a141c9448362072',
 'scripts/clickhouse/provision_backtest_v4_ladder_runner.py': 'ef34b7dfc73d05919f04b55d616f4950b7f6f3547a88e3a2d342ed2e6ab57f8e'}


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
