"""Full inherited50 armed profit extension proof; closed until source review."""
import ast
from hashlib import sha256
import json
from pathlib import Path

REQUIRED_SOURCE_FILES = ('pipelines/strategy_one/configuration_publisher.py',
 'pipelines/strategy_one/strategy_fifty_two_configuration.py',
 'scripts/clickhouse/publish_strategy_fifty_two_configuration.py',
 'scripts/clickhouse/report_strategy_one_trades.py',
 'src/backend/backtest_declared_initial_momentum.py',
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
 'src/trading_runtime/arte_first_price_entry_v4.py',
 'src/trading_runtime/arte_followthrough_failure_v4.py',
 'src/trading_runtime/arte_initial_momentum_entry_v4.py',
 'src/trading_runtime/arte_journal_commit_v4.py',
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
 'src/trading_runtime/numbered_fixed_strategy.py',
 'src/trading_runtime/runtime.py',
 'src/trading_runtime/strategy_confirmed_ah_failure_exit.py',
 'src/trading_runtime/strategy_confirmed_ah_failure_source.py',
 'src/trading_runtime/strategy_fifty_release.py',
 'src/trading_runtime/strategy_fifty_two_contract.py',
 'src/trading_runtime/strategy_fifty_two_release.py',
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
 'src/trading_runtime/strategy_rising_momentum_witness.py')

# Reviewed complete native execution, independent cold proof and release routes.
STRATEGY52_SOURCE_AST = {'pipelines/strategy_one/configuration_publisher.py': 'bebbde79b2ad694092222e96d4e09bd50cb3579295a9ca85bf2fcbcb8ff6dc1f',
 'pipelines/strategy_one/strategy_fifty_two_configuration.py': 'c875e06b784caefbc2a1db81e7f914e502ba34fea86dcd4ddd592ea872f84dcc',
 'scripts/clickhouse/publish_strategy_fifty_two_configuration.py': 'a63f53f0738d6b65de4cecd621829982d7b9420d43c376bf7e3e19a2b838d2ec',
 'scripts/clickhouse/report_strategy_one_trades.py': '1221071128e4c18135f10ec9322a88af2e15e941dc4d6a9e9dfd182afa1de576',
 'src/backend/backtest_fixed_v4_certification.py': '8404b3f0c830bc6e60e6b8665d46f2b29ce3da83f53b1aa0d8e056649a0cb6e2',
 'src/backend/backtest_journal_memory.py': '13c94e0045a47141e5b9bd708ee851a267e104c78ae0618cdadfc469bda44eaa',
 'src/backend/backtest_strategy_certified_price_break.py': '8d6a7a26fc783252033f882544312eef55a5407b95e60259640452f9f42891ac',
 'src/backend/backtest_strategy_episode_activity_source.py': '0a1bb6874e6e36d377909dfad350b700095637e90cedc4bd4ac426ec9d891444',
 'src/backend/backtest_strategy_liquidity_fade.py': '5e09a1f14b27d0e63bfff7e46ba5487cc1b740c8373a78425f7765513f68b05a',
 'src/backend/backtest_strategy_liquidity_fade_loader.py': 'ad9176e7c167a2a20adc6f95e3d2bc67dd64d53d419301b347dd8ead49721172',
 'src/backend/backtest_strategy_one_configuration.py': 'e7ac8ac056735d57086db293f47d915a7a987cf0e0e91d428e7117e494399f3f',
 'src/backend/backtest_strategy_one_coordinator.py': '188d9b1853e0ffc7b7fe913728d96c60cb27d800a7cfd6e9b61cca97567c52b4',
 'src/backend/backtest_strategy_one_execution.py': '1e3731185f9c0d59cb7d13cb15d58501eb446ffffed7ffcb9da013145141309e',
 'src/backend/backtest_strategy_one_management.py': '8001c4c43ca9775c3b3b45087c55fc36f9786844f6a19b698cc445393e6cc7db',
 'src/backend/backtest_typed_projection.py': '6c0367a9a96836c595fd7e2dda9654b163ac82e072bd370d579da699533cc9e9',
 'src/backend/backtest_typed_publisher.py': 'dc3b8c9c091c7d4bdee9c75c5df875d9110df643a5c7b85037c7f3287a960724',
 'src/backend/backtest_v4_saved_review.py': 'dad8f6f3351f02641917487fa3f6b592ae1e863f16786c5e64b74f77881cd348',
 'src/backend/replay_run_service.py': 'e7eb5b359f9126316ed99721ade0fa50f8a1b8f8eccd99b96b1225ac387b9b86',
 'src/trading_runtime/arte_confirmed_ah_failure_v4.py': '891173466e4c706d3f665c2facfcfb67f27d9d9671ef100264c664d197894b4e',
 'src/trading_runtime/arte_entry_activity_v4.py': 'd1925f80bf6309e040d257604a7dcaf890e00e471fb822218cfa1418bb1b52d3',
 'src/trading_runtime/arte_first_price_entry_v4.py': '13934841d1f1a36c7eaa3df3a5a95f130c29e433d2f5eb56b6941f60fe89526a',
 'src/trading_runtime/arte_followthrough_failure_v4.py': '8a1daf50731f83bd20e32c0e0db4e0f5c3074c4a1030ab87528a7e964b614907',
 'src/trading_runtime/arte_initial_momentum_entry_v4.py': 'cc57d1ec7e23ad0ffd8eb183abb35badaeb8b7257a943768c146fd7c10f69888',
 'src/trading_runtime/arte_journal_commit_v4.py': 'afd19d7c6d5326fe6de546375f13f793f04a86048d10be799d44cf4f1eafeca4',
 'src/trading_runtime/arte_journal_writer.py': '282b9e885d708f0e27acda1aa8ab379b98dea07885324ac71585a74fab04048b',
 'src/trading_runtime/arte_liquidity_fade_failure_v4.py': 'a855346506ab20095e365cf306ea063cdbd62d60f929951c5c0dc73251c93dc8',
 'src/trading_runtime/arte_oms_projection.py': '2320e8877a34c57a4d3dec38a45a48b540bf0577d995b1837e1246db6a66eed6',
 'src/trading_runtime/arte_profit_giveback_v4.py': '6b188986455e90783833f8e34f0e9c845400fa7cf29cf85b2f003872fab946ab',
 'src/trading_runtime/arte_rising_momentum_entry_v4.py': '38ba23e6f86c22d30515b74258f877aa94e78b10ffa8e9ee6890050d28be68e4',
 'src/trading_runtime/arte_strategy_one_entry_journal.py': '2530ac648d393f7656b19ca1be5bb71cc89a11173b1886437a7f0ec7faae014c',
 'src/trading_runtime/declared_followthrough_failure.py': '68c2ed0f236db57cf07b557a331448b77a35de9c0645f5e7ec5adde51e1f9920',
 'src/trading_runtime/declared_profit_giveback.py': '944de8d53f75e5f270120fa36d5d8786a6ea167ed824f1d8e501c8a9ae120bc3',
 'src/trading_runtime/early_original_risk_failure.py': '1df39278ef45fd7392fa570702b836588c21274aa0eb36c3667477dbcef0dc89',
 'src/trading_runtime/numbered_fixed_strategy.py': '295c5db3012f008dee16be92633884c974ad2315120752c86c86f5ccf718335d',
 'src/trading_runtime/runtime.py': 'a6d176b98c1107ede3ec7d09edeec555ed069806159e908d134bb51bd8bb7210',
 'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '22b871c00d88dfd540b2bed112dbf711f3f725beedda76fd6bd07212bc6b3809',
 'src/trading_runtime/strategy_confirmed_ah_failure_source.py': '97ad08801dbf22043a215de71a0f0d0f293635e3ad7263f90e0f6b46a68a850a',
 'src/trading_runtime/strategy_fifty_release.py': '2f078566963e645c725d14526621e87a99d623f415fa010aa2440921e8d87f1b',
 'src/trading_runtime/strategy_fifty_two_contract.py': 'bc88c34f6b428fff15a405f3dd3b8144f074874f9840ac8bca6a227ce40f1227',
 'src/trading_runtime/strategy_fifty_two_release.py': '25bc0fe8158e1915f709fdb1fc84bbb201af8fbb82fa53a064fa0920bd4da4c3',
 'src/trading_runtime/strategy_followthrough_exit.py': '6faac9cae66c139869dad4c3be31d4b9a40efa3e3984bfb8e0bdc088d277d450',
 'src/trading_runtime/strategy_half_risk_liquidity_fade.py': 'c1bf6713225655156c8c8d62bd6e78195c3edb0ab5d040e956c7de1be61de049',
 'src/trading_runtime/strategy_liquidity_fade_exit.py': '64fbc594888256b65007f085d713f69fbe8fc47978ee38e2d66553e63839dabd',
 'src/trading_runtime/strategy_liquidity_fade_publication.py': '4c60848f85c7c04f09f0a6893555769595289e9142cab64b109ef07e16fe21df',
 'src/trading_runtime/strategy_liquidity_fade_source.py': '19908ce5ae7655dd9bafdb46ce60308b4ee532952c505b893d956dae552cfdd5',
 'src/trading_runtime/strategy_one_management_snapshot.py': '32ecf7eb291ee5953dee6fbbac99649e4b8217d1ac9e51ad4a646438933650a6',
 'src/trading_runtime/strategy_profit_giveback.py': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10',
 'src/trading_runtime/strategy_profit_giveback_arm.py': 'bbb9ea954eef8212196fa6b31907f5c0e2209e8c6966cd9a8aad3161b5612d64',
 'src/trading_runtime/strategy_profit_giveback_exit.py': 'eda77e55bdbddc2bc67969107f54253379deba37aec61d104de492f58b6c882e',
 'src/trading_runtime/strategy_profit_giveback_source.py': 'b7223c7d10e9670362b8540bed75eb00950391523c6907c7b07921a8d2c3cfc2',
 'src/trading_runtime/strategy_registry.py': 'a93f5cde8cbf06e0c1af27ff3d8d097f3eb7e869644025dba4fa187f74ebcad4',
 'src/trading_runtime/strategy_rising_momentum_witness.py': '6d5bd7f4a11ae8f2e05e4138a29d7cde0c53efb8945b38cfe117f82d1d05c4ee',
 'src/trading_runtime/entry_momentum_growth.py': '8acec288e9cf7d1c62bf8cb2fa386f82d5db69a63fc9306b90168f8cee3c0b5c',
 'src/backend/backtest_declared_initial_momentum.py': '786a12c62a157c092652c8cd7d695fe4f5e8ebfb315c9b8dbab56580fb0a4b81',
 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995'}


def certify_strategy_fifty_two_source(*, source_overrides=None):
    overrides = source_overrides or {}
    if set(STRATEGY52_SOURCE_AST) != set(REQUIRED_SOURCE_FILES):
        raise ValueError('Strategy52 complete source review is not sealed')
    if set(overrides) - set(STRATEGY52_SOURCE_AST):
        raise ValueError('Strategy52 source override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in STRATEGY52_SOURCE_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Strategy52 source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy52 pinned source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
