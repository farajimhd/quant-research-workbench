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
    'src/trading_runtime/arte_entry_activity_v4.py': 'f8a88dc959b1ea633d0e8bea1592f9fcecd41542c1581e81796c58ad2b95e28e',
    'src/backend/backtest_strategy_episode_activity_source.py': 'd3ce41aef76211b799df01cb1dee6efd6a1df065610c203c4790ae87a58d0797',
    'src/backend/backtest_strategy_episode_activity_gate.py': '43eaa3e95701f32ab02f5af501ca0e019b03ed91badb8f1bcb62ced3918510b5',
    'src/trading_runtime/strategy_episode_activity_veto.py': 'cb96806e4594bdb2b087b1c12925ee80d296e1313984dce6329f3eee4b911e06',
    'src/backend/backtest_strategy_entry_activity_gate.py': 'a128c7856cbc77854871961ec4d582b5f44b9bdeee517f6dde61360811fd02d4',
    'src/backend/backtest_strategy_entry_activity_source.py': 'db6e9f16dd62c48f98dc5ec8f7bfa40a17f69974bb618097418ec3f246d91681',
    'src/trading_runtime/strategy_thirty_six_release.py': '9a243bfe59b4071497bac22231ac0fb104262af40ff282e4910e6819a8b29308',
    'pipelines/strategy_one/strategy_thirty_six_configuration.py': '4a03259d1ab4a0ae29a2a14672fdef94249cac0b614478817b9f22d8a91d34b6',
    'pipelines/strategy_one/configuration_publisher.py': '939e753aae2e760eeb717ba57e60a039a8a58642de3a41b6b36d16b8f3c0af42',
    'src/backend/backtest_strategy_one_configuration.py': '87df1c3224fed7ea9ac5b4022f0937b07266bdfc902055c00e777ca951fa95d2',
    'src/trading_runtime/strategy_registry.py': 'dd185fbef65cbfd90a530e743c358e82decb6441c0a905f8bd8ff12078090d70',
    'src/trading_runtime/numbered_fixed_strategy.py': '6fbf9d79466681d33d1814582afd30870617ac70853b3bcc9eabe7fa31d6c36d',
    'src/backend/backtest_strategy_certified_price_break.py': '5e80003b263b24e07f4b6033e03ac769b95b5065daa86b671de982516234f03a',
    'src/backend/backtest_strategy_one_execution.py': 'ae6bcd3071cff80292b89022760e228169b6b098d7f6c906f5b937b8228231f7',
    'src/backend/backtest_strategy_one_coordinator.py': 'caee04929095369454fbcb9a21a08dab806a46216dd12d1f6beef8fa975fb77c',
    'src/backend/backtest_strategy_one_management.py': '3a6399c1148f736b23d925a82055e801818cb61c6333f8bc2635db00c5697401',
    'src/trading_runtime/strategy_one_management_snapshot.py': 'a8ca8cc1a20e04266dd119171324fa69e73c9980b816976f89e1cc0a66dd2418',
    'src/trading_runtime/runtime.py': 'b6d5daad1bbc5b14cc49dc0877eb6827db5a36ff5353d347b606a31c55b23581',
    'src/backend/backtest_journal_memory.py': 'f393b9d76a07a3f1e59dc428ef65b738c4a6e1cd83caee9735c7c8da3aedac67',
    'src/backend/backtest_typed_projection.py': '021a22a44902a013d37cf91b68a912f0cc1db41f60ed90dfcbc0470611c4f913',
    'src/backend/backtest_typed_publisher.py': '9ede7101106036958685b0dd00d38b95c8bbd52f42950470c5d6b5751dc6d8af',
    'src/trading_runtime/arte_strategy_one_entry_journal.py': '16b63cc31b47e7498ca18e1bc736bd88c7e315b7ea9428ac303bd0239c892d7b',
    'src/trading_runtime/arte_journal_writer.py': 'c21615565e30f799828d3fa86a8d0824e614c34a928127e671f1e402a2035b6a',
    'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
    'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
    'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
    'src/trading_runtime/arte_journal_commit_v4.py': 'b5037a969d67089413ae42f458aeb3fb430914b26df1fabd2595203275b33908',
    'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
    'src/backend/replay_run_service.py': '9c0f5276f220d163deb5e10c75a05343954727f9ccff2f073d278146e2cedf94',
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
