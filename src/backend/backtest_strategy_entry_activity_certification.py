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
    'src/trading_runtime/arte_entry_activity_v4.py': 'b2aee8a3c6abc38b203e279b27abdc5e731a3c9ac56cf9990ff6e3a34cff04e5',
    'src/backend/backtest_strategy_episode_activity_source.py': 'd85cb7c019f6903e6164de80babe44cb19a12fd8a43daee3d0d6ddbc22fbc835',
    'src/backend/backtest_strategy_episode_activity_gate.py': '43eaa3e95701f32ab02f5af501ca0e019b03ed91badb8f1bcb62ced3918510b5',
    'src/trading_runtime/strategy_episode_activity_veto.py': 'cb96806e4594bdb2b087b1c12925ee80d296e1313984dce6329f3eee4b911e06',
    'src/backend/backtest_strategy_entry_activity_gate.py': 'a128c7856cbc77854871961ec4d582b5f44b9bdeee517f6dde61360811fd02d4',
    'src/backend/backtest_strategy_entry_activity_source.py': 'db6e9f16dd62c48f98dc5ec8f7bfa40a17f69974bb618097418ec3f246d91681',
    'src/trading_runtime/strategy_thirty_six_release.py': '9a243bfe59b4071497bac22231ac0fb104262af40ff282e4910e6819a8b29308',
    'pipelines/strategy_one/strategy_thirty_six_configuration.py': '4a03259d1ab4a0ae29a2a14672fdef94249cac0b614478817b9f22d8a91d34b6',
    'pipelines/strategy_one/configuration_publisher.py': '6fff67894cd08c65969b9ebf26e58db942db8f6d80fb588b3e93c9544ace8a2f',
    'src/backend/backtest_strategy_one_configuration.py': 'a5697d4122393cb49ed38a68764b1bc0971d4b77e78d442855ad8abd2628ba58',
    'src/trading_runtime/strategy_registry.py': 'a767b6a68e1d38819e9b065b576a592b97e888eb66d9e7459029f145456097b3',
    'src/trading_runtime/numbered_fixed_strategy.py': 'c47f675530b92b67c9553919990f58ad7ea7abc171a2b5f4eff04f98f28d2051',
    'src/backend/backtest_strategy_certified_price_break.py': '2b5e8abcf5caab0f6d50d5bcc1ec9fd26f712f1a3cdf25ef373fc46c9c046174',
    'src/backend/backtest_strategy_one_execution.py': '4e71f044f4deb794115be1cc81f3bb3eb59c0f588a56959da5c09ee6193830ad',
    'src/backend/backtest_strategy_one_coordinator.py': '7494305fae29254ca2d483876b22f65be581faabe791a71e9bdba0659a289071',
    'src/backend/backtest_strategy_one_management.py': 'a0b48d7649060d0cb08f6816966306b64ce819c5660c323a9ee0b4393e1a23eb',
    'src/trading_runtime/strategy_one_management_snapshot.py': '67bb28045cacae27fe8faf5ffe460e983248cc539447e511bf688dd6bd642361',
    'src/trading_runtime/runtime.py': 'c6dd993c3fa5b32bb0cf0414d0df1f92c487aff7dfeb9d9c1374f24cc89cb2b5',
    'src/backend/backtest_journal_memory.py': '824a8f8803af1d06073f509644d25cb50052379f24a259adc7ac819920d9e006',
    'src/backend/backtest_typed_projection.py': 'bd282ce151c36235eb3a881ecb14c26a6b463d35ebd934d9420b9fa015bf8af9',
    'src/backend/backtest_typed_publisher.py': 'c668f99f94a5c52da8fced7cb10c8c33bd1f32e76f784b7853e956db891f3613',
    'src/trading_runtime/arte_strategy_one_entry_journal.py': '0d74c5c83b0c1130a9162dcf7c44376d62fa24eb2d152411c82fb2bcc30fdb8c',
    'src/trading_runtime/arte_journal_writer.py': 'ec1faf4bde235da17a4873bbf6db4853be8751c2bce45d39f4637c3565bb7b7b',
    'src/trading_runtime/arte_journal_commit_v4.py': 'd5efc33b654317b3f50ac36b01b34c518fcd5db4f03bd8a3990dce344d580337',
    'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
    'src/backend/replay_run_service.py': '58ef03a63bb3bfc57d998cd55f68b7c0357201e74b11b5b04d670fa543813e25',
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
