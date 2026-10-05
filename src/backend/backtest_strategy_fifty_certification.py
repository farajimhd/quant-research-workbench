"""Full inherited42 weak-positive early failure seal; populated only after all coordinated lanes are reviewed."""
import ast
from hashlib import sha256
import json
from pathlib import Path

REQUIRED_SOURCE_FILES = ('pipelines/strategy_one/configuration_publisher.py', 'pipelines/strategy_one/strategy_fifty_configuration.py', 'scripts/clickhouse/publish_strategy_fifty_configuration.py', 'scripts/clickhouse/report_strategy_one_trades.py', 'src/backend/backtest_fixed_v4_certification.py', 'src/backend/backtest_journal_memory.py', 'src/backend/backtest_strategy_certified_price_break.py', 'src/backend/backtest_strategy_episode_activity_source.py', 'src/backend/backtest_strategy_liquidity_fade.py', 'src/backend/backtest_strategy_liquidity_fade_loader.py', 'src/backend/backtest_strategy_one_configuration.py', 'src/backend/backtest_strategy_one_coordinator.py', 'src/backend/backtest_strategy_one_execution.py', 'src/backend/backtest_strategy_one_management.py', 'src/backend/backtest_typed_projection.py', 'src/backend/backtest_typed_publisher.py', 'src/backend/backtest_v4_saved_review.py', 'src/backend/replay_run_service.py', 'src/trading_runtime/arte_confirmed_ah_failure_v4.py', 'src/trading_runtime/arte_entry_activity_v4.py', 'src/trading_runtime/arte_first_price_entry_v4.py', 'src/trading_runtime/arte_followthrough_failure_v4.py', 'src/trading_runtime/arte_initial_momentum_entry_v4.py', 'src/trading_runtime/arte_journal_commit_v4.py', 'src/trading_runtime/arte_journal_writer.py', 'src/trading_runtime/arte_liquidity_fade_failure_v4.py', 'src/trading_runtime/arte_oms_projection.py', 'src/trading_runtime/arte_profit_giveback_v4.py', 'src/trading_runtime/arte_rising_momentum_entry_v4.py', 'src/trading_runtime/arte_strategy_one_entry_journal.py', 'src/trading_runtime/declared_followthrough_failure.py', 'src/trading_runtime/early_original_risk_failure.py', 'src/trading_runtime/numbered_fixed_strategy.py', 'src/trading_runtime/runtime.py', 'src/trading_runtime/strategy_confirmed_ah_failure_exit.py', 'src/trading_runtime/strategy_confirmed_ah_failure_source.py', 'src/trading_runtime/strategy_fifty_contract.py', 'src/trading_runtime/strategy_fifty_release.py', 'src/trading_runtime/strategy_followthrough_exit.py', 'src/trading_runtime/strategy_half_risk_liquidity_fade.py', 'src/trading_runtime/strategy_liquidity_fade_exit.py', 'src/trading_runtime/strategy_liquidity_fade_publication.py', 'src/trading_runtime/strategy_liquidity_fade_source.py', 'src/trading_runtime/strategy_one_management_snapshot.py', 'src/trading_runtime/strategy_profit_giveback_arm.py', 'src/trading_runtime/strategy_profit_giveback_exit.py', 'src/trading_runtime/strategy_profit_giveback_source.py', 'src/trading_runtime/strategy_registry.py', 'src/trading_runtime/strategy_rising_momentum_witness.py')

# Reviewed complete native execution, independent cold proof and release routes.
STRATEGY50_SOURCE_AST = {'pipelines/strategy_one/configuration_publisher.py': '2bda267ca9ea242d6be5d30460d0d66c37b3bf6935867343a201b8f20d03f00b',
 'pipelines/strategy_one/strategy_fifty_configuration.py': 'bde8696f70d4dfd16c12781832fb6e8b566482c4f7c5e2460c62cb3a9634bff9',
 'scripts/clickhouse/publish_strategy_fifty_configuration.py': '070b8ce29c010b6ebf9448510b916ed97a186838e8b9ce048ae6694e43b4ac07',
 'scripts/clickhouse/report_strategy_one_trades.py': 'ee29f3c65ff43877f350922d69c9e2f779b6da3cd16a5fd6f02c5636fc950b5d',
 'src/backend/backtest_fixed_v4_certification.py': 'fac72eb1efc6fc71f414c3d85215740ead5d83db70d92481509b43176cb26005',
 'src/backend/backtest_journal_memory.py': '717a5e4a4f5a31806282b3f7b37ba88c04e3862cd8413d49ff0f1bf79bc1277e',
 'src/backend/backtest_strategy_certified_price_break.py': 'c11b20c8ed88b2d3350da6a494d5209176069507ae2f6a47a4529f9baf50b6c6',
 'src/backend/backtest_strategy_episode_activity_source.py': '157dee72c0c99d75fdc40ee3af33a34c034ebe53e8688236a622dbf4ea07d010',
 'src/backend/backtest_strategy_liquidity_fade.py': '299861f1a5980e9c3a2771f26e22737052b2b3059735169adc96690c38ea1ca7',
 'src/backend/backtest_strategy_liquidity_fade_loader.py': '67bc58b3ce70907e71cb26ffc9dd4b928da4ce30f7a65625c3b6c1db3c93e5d1',
 'src/backend/backtest_strategy_one_configuration.py': 'd5a2bc061571e09ae55ea611e7e99f5e608f9ab9dec7e350d2742d7fca3224c9',
 'src/backend/backtest_strategy_one_coordinator.py': 'e3f87d0517f559cdf90de7407092d3aad20e0a622cb551291257dc28f791b481',
 'src/backend/backtest_strategy_one_execution.py': '4dfbb938081cdcdd651bd2a148f73e498338df141a20f162bc5507e0e3c77852',
 'src/backend/backtest_strategy_one_management.py': '5eba2fbe247ced68f8d62ff653ceb68fa1486e8e0dbdf00140c88f32604b10c5',
 'src/backend/backtest_typed_projection.py': '22ab5cb4116e3509b27c882a6234adb16e38e9853146014d9cb4b3eca4bdba68',
 'src/backend/backtest_typed_publisher.py': 'a211e08727e013d4429582960c2e9c60a9d82348d4bb2248aaf2a189ea864be6',
 'src/backend/backtest_v4_saved_review.py': 'a66e597fafaebc7650b811b83f06f6f2092136e41a21e560313492937c162389',
 'src/backend/replay_run_service.py': 'a98a1067c73fb30daa6c17dfb4c07aadf9fb253ad595da32af8f373173fdc215',
 'src/trading_runtime/arte_confirmed_ah_failure_v4.py': '9dc51903b6016a819f2c457d84872bea440ed6ae20a2b355fb97da83229c0f20',
 'src/trading_runtime/arte_entry_activity_v4.py': '1ecbca6580086078aa1c9364f65d323961173a61df0b7bae6aa631a8f93812eb',
 'src/trading_runtime/arte_first_price_entry_v4.py': '72076e003f9cf6f70af5427372d3922dd2ffe7f59171050de723f1f99eb3d96c',
 'src/trading_runtime/arte_followthrough_failure_v4.py': '5e69522f0843ec8e275a6b8ef8066609f72e082391e76089cecff7dd66c042a8',
 'src/trading_runtime/arte_initial_momentum_entry_v4.py': 'f69d534bf4344bed1cd946fab9d8d70716ce6b3ad8d6792dd7acfbbc2c2d8a59',
 'src/trading_runtime/arte_journal_commit_v4.py': 'c1f3fa36c9e782ba6badb204b85ffe58acdfe044e40f52228c22b2e6aef5c3eb',
 'src/trading_runtime/arte_journal_writer.py': '6c7b13c39de95592840b2224c202bb0b00e996076612bd6514e10b6c0d93204f',
 'src/trading_runtime/arte_liquidity_fade_failure_v4.py': '5a7a513a712f46b76049af7290fdbcb1f04d96d580d0861db4d76239975779f5',
 'src/trading_runtime/arte_oms_projection.py': '12d74f7a51be8ee01483ef8f0147b9d77fb878823c6fd6f3b4757c4167b99caa',
 'src/trading_runtime/arte_profit_giveback_v4.py': '9486fcfd61799446b81abd8e00550e0858bd6c197cb09ba209c0735261d7cd9e',
 'src/trading_runtime/arte_rising_momentum_entry_v4.py': '6d7fb1625517c6a48c0cedbda15ffaa67037237defa7e8e0456dc5b5ed56d692',
 'src/trading_runtime/arte_strategy_one_entry_journal.py': 'f53c55086ff39bbd10e99568c1b50eea649dad2850888b8bf68d0bebf6a35e90',
 'src/trading_runtime/declared_followthrough_failure.py': '68c2ed0f236db57cf07b557a331448b77a35de9c0645f5e7ec5adde51e1f9920',
 'src/trading_runtime/early_original_risk_failure.py': '1df39278ef45fd7392fa570702b836588c21274aa0eb36c3667477dbcef0dc89',
 'src/trading_runtime/numbered_fixed_strategy.py': 'e77afb232629caaf7cd736f3e42ffda9e71690003fd3943b690b142742c55d6f',
 'src/trading_runtime/runtime.py': 'c932991d4183d8dd42999157e8370d5c0e57ce411736e8de45ce1f64fc740a2e',
 'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '22ed83b6d42e085a8b8b59ac40fe3a3ce06e29e9fded220fa8d75e89a2bff66e',
 'src/trading_runtime/strategy_confirmed_ah_failure_source.py': 'f1fcc57eddb5722116e49acf7d4a2d815d8ceb34fbbe10d44b0699cdc66c3231',
 'src/trading_runtime/strategy_fifty_contract.py': 'de010651cca50ec4995d75c26a03c2818cae65ca7a8b998ef741a91bcaa9a1a2',
 'src/trading_runtime/strategy_fifty_release.py': '2f078566963e645c725d14526621e87a99d623f415fa010aa2440921e8d87f1b',
 'src/trading_runtime/strategy_followthrough_exit.py': '68ede578ca6668621ba44b7c9bf1c298f8c963de8bd05a3786942e2de4a3447b',
 'src/trading_runtime/strategy_half_risk_liquidity_fade.py': 'e5a16376fb84f698afb8ef13f5a59b9af75ccc5aab8836cd7b69faeaab0028b4',
 'src/trading_runtime/strategy_liquidity_fade_exit.py': '707a5b99826c04e7c807cde9f7d9c494a57f1612495a04dd2d73592e22ad63d7',
 'src/trading_runtime/strategy_liquidity_fade_publication.py': '615203cb7e80b42a0c179ee21bd0cecab5349d1cb4c0ef4254716f98b38b8df5',
 'src/trading_runtime/strategy_liquidity_fade_source.py': 'dd44d3ae2dfefb914acd8aa1f8dcdc3a40625349dbe05ca9d387b4d86ba732f5',
 'src/trading_runtime/strategy_one_management_snapshot.py': '76102fb098cbd89d47ec86849597aae7b0fc65f79263ace8455d30d79c43eb73',
 'src/trading_runtime/strategy_profit_giveback_arm.py': '19c91475dc45da258bab57f6f27c2dd7fde8fdae3448f3245c0ac83ed28525d6',
 'src/trading_runtime/strategy_profit_giveback_exit.py': '62f82e3e86c45eee06f382b17e0741c44374810688e2ff17207b72750abeddc4',
 'src/trading_runtime/strategy_profit_giveback_source.py': '2c278cd711240f22f24a491231bfc49a2366323a966f4e49f1c9d900a1a98ac2',
 'src/trading_runtime/strategy_registry.py': '5d67e5a268fb2b07824c733033a9907237ba05dc962289cc51f3262333dc1efe',
 'src/trading_runtime/strategy_rising_momentum_witness.py': 'b2eaa49ecc1cd062e02a44fc37145b1fa2f72daacdbffce6c08e450a4fbf26a6'}


def certify_strategy_fifty_source(*, source_overrides=None):
    overrides = source_overrides or {}
    if set(STRATEGY50_SOURCE_AST) != set(REQUIRED_SOURCE_FILES):
        raise ValueError('Strategy50 complete source review is not sealed')
    if set(overrides) - set(STRATEGY50_SOURCE_AST):
        raise ValueError('Strategy50 source override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in STRATEGY50_SOURCE_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Strategy50 source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy50 pinned source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
