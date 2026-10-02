"""Source seal for Strategy36 entry activity, publication and execution routes.

The complete release certificate composes this with the full Strategy35 proof.
This source seal alone establishes neither market coverage nor profitability.
"""
import ast
from hashlib import sha256
import json
from pathlib import Path


ENTRY_ACTIVITY_SOURCE_AST = {
    'scripts/clickhouse/publish_strategy_thirty_six_configuration.py': '9181d082d60d5269e13b69448febc5100b955ff42a9a9acbce0735f28bfd71a9',
    'src/trading_runtime/strategy_entry_activity_fade.py': '78219596eedb2c3de41ed0597f4e5bde5efea9d68854645f2786ad53fa5c4b75',
    'src/trading_runtime/strategy_entry_activity_witness.py': 'ce4bfd96579271bd7d7124e700c40824e9c870266ff05d9ea10b50bcb4d72bc8',
    'src/trading_runtime/arte_entry_activity_v4.py': 'a187928dd637c8ef350ffc751165ff5835507630ce1ff5de4bd247269b0e175a',
    'src/backend/backtest_strategy_episode_activity_source.py': '9d53ade77cdcaac72e097968ad90bbedce4beaaf8866ca4c2f6104d441f7788a',
    'src/backend/backtest_strategy_episode_activity_gate.py': '43eaa3e95701f32ab02f5af501ca0e019b03ed91badb8f1bcb62ced3918510b5',
    'src/trading_runtime/strategy_episode_activity_veto.py': 'cb96806e4594bdb2b087b1c12925ee80d296e1313984dce6329f3eee4b911e06',
    'src/backend/backtest_strategy_entry_activity_gate.py': 'a128c7856cbc77854871961ec4d582b5f44b9bdeee517f6dde61360811fd02d4',
    'src/backend/backtest_strategy_entry_activity_source.py': 'db6e9f16dd62c48f98dc5ec8f7bfa40a17f69974bb618097418ec3f246d91681',
    'src/trading_runtime/strategy_thirty_six_release.py': '9a243bfe59b4071497bac22231ac0fb104262af40ff282e4910e6819a8b29308',
    'pipelines/strategy_one/strategy_thirty_six_configuration.py': '4a03259d1ab4a0ae29a2a14672fdef94249cac0b614478817b9f22d8a91d34b6',
    'pipelines/strategy_one/configuration_publisher.py': '3021ab1f454ea0d74f36623c2f7aaaeafe917a7abb56116d39aa127f02400f5f',
    'src/backend/backtest_strategy_one_configuration.py': 'c29449ca87d14d3b66c4f644c7ba286fdad6423101b4b3f6ccf935c6ae74cce4',
    'src/trading_runtime/strategy_registry.py': 'afa46ca933c2119456fd0c7aaf9a1d4f1e050aa5e7653583dd09203f3bd401a5',
    'src/trading_runtime/numbered_fixed_strategy.py': '5d13530df770e50eaca93e3e92450d690a12404e070d93140e5a608117802268',
    'src/backend/backtest_strategy_certified_price_break.py': '1d6e254ba400f3d17f7d80059e8f4bac36c9f04988d6bde7f9460229e75bfaca',
    'src/backend/backtest_strategy_one_execution.py': 'eb79647935683952adfb6735e2eb0004e5e418dab1cb1f149813ee29270aff4c',
    'src/backend/backtest_strategy_one_coordinator.py': '4ef162fc1e65ad08213d6b51b82a5d71a77627d38d75a5f8f6b3d0cbe987ca2c',
    'src/backend/backtest_strategy_one_management.py': 'e7a69fad14bba450e08a84ce602c621401d76f9671afdb1231abe569acb65818',
    'src/trading_runtime/strategy_one_management_snapshot.py': 'fb43bc59d173903a802ffcc9510fcde7e6630456f8ee4dba8e832a171e9a56f9',
    'src/trading_runtime/runtime.py': '96c7b16f3d707bd92a7c47124e8336de9538907109a7b7bb3cd21af69e8a8b65',
    'src/backend/backtest_journal_memory.py': 'edf5c200c98b8e2f021d96952d2a79fe5f46e9085f7d580fe0d4f7c7fe605cba',
    'src/backend/backtest_typed_projection.py': 'e3915158079fdace8c1138a19baefe41444933ad17b4d771bfe5972ba5c9da5c',
    'src/backend/backtest_typed_publisher.py': '576119861b90a0931e75347ad81f7465fc1fa4d2542744e817b36dbc9a4e7df0',
    'src/trading_runtime/arte_strategy_one_entry_journal.py': '29c9aa7b3b5855b101a363befbaac1a66df50e501866c601f5485c33f689aad5',
    'src/trading_runtime/arte_journal_writer.py': 'b91b820d2fd5144f47a92fc80a0bd7bff78216f028c499cbbba53fbd7000cc96',
    'src/trading_runtime/arte_journal_commit_v4.py': '98f316de4da04fde500044328624aa4f96237cda44ac7a1e76151879e848c25a',
    'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
    'src/backend/replay_run_service.py': '630ac301b866f7471e80082f29d5721672bb60ab8ef9ac2e3e429f9e64170605',
}


def certify_entry_activity_source(*, source_overrides=None):
    """Fail closed on changes to any reviewed additional authority."""
    overrides = source_overrides or {}
    if set(overrides) - set(ENTRY_ACTIVITY_SOURCE_AST):
        raise ValueError('Entry activity source override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in ENTRY_ACTIVITY_SOURCE_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Entry activity source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Entry activity source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
