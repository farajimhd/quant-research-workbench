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
    'src/trading_runtime/arte_entry_activity_v4.py': '11c39b7724ead17b4c4556752c845bc3d1ddc168c9fd53be550ddb923138b3ab',
    'src/backend/backtest_strategy_episode_activity_source.py': '36b3d5b56574be16056d64a4a620b6e73603ae9da1bebbb4aef811272d32352b',
    'src/backend/backtest_strategy_episode_activity_gate.py': '43eaa3e95701f32ab02f5af501ca0e019b03ed91badb8f1bcb62ced3918510b5',
    'src/trading_runtime/strategy_episode_activity_veto.py': 'cb96806e4594bdb2b087b1c12925ee80d296e1313984dce6329f3eee4b911e06',
    'src/backend/backtest_strategy_entry_activity_gate.py': 'a128c7856cbc77854871961ec4d582b5f44b9bdeee517f6dde61360811fd02d4',
    'src/backend/backtest_strategy_entry_activity_source.py': 'db6e9f16dd62c48f98dc5ec8f7bfa40a17f69974bb618097418ec3f246d91681',
    'src/trading_runtime/strategy_thirty_six_release.py': '9a243bfe59b4071497bac22231ac0fb104262af40ff282e4910e6819a8b29308',
    'pipelines/strategy_one/strategy_thirty_six_configuration.py': '4a03259d1ab4a0ae29a2a14672fdef94249cac0b614478817b9f22d8a91d34b6',
    'pipelines/strategy_one/configuration_publisher.py': '451c9ffbc8ac2f0a79be23f0dbecfb043cb840343b18e1fde03ec8feb3fab0fc',
    'src/backend/backtest_strategy_one_configuration.py': '07d3131898357c5badefbd95612cb784357e5786c9194ad006bb5bfb9773d4c6',
    'src/trading_runtime/strategy_registry.py': 'f0eeecab59f478c0643e7d443b0b37d5cf4dcba0e1a53989f417e87bc38239aa',
    'src/trading_runtime/numbered_fixed_strategy.py': '334007da600878df78cca37e06fda2a447f34f7c56a7b71b7b912cbd275a82e4',
    'src/backend/backtest_strategy_certified_price_break.py': 'c46680005979c91006d9ee48d47c04631fd513cec69778472dfe6ca5dcb03e58',
    'src/backend/backtest_strategy_one_execution.py': 'b924c30ac06bb2a12edea845377e31ad5269154296ab7207f532ad0f38b12a80',
    'src/backend/backtest_strategy_one_coordinator.py': '596d97ff1207be94508c1918b108bdda83f5dd2930d7aef7c09114a11a4e1ea5',
    'src/backend/backtest_strategy_one_management.py': '4eb215447f0ac551c02cf7a6f5d357be5053d71ece106b3b5548947f97f62448',
    'src/trading_runtime/strategy_one_management_snapshot.py': '0b3d19cc50cc65f5cc011e3f2357adc015b73cbe32788c780c4b15b245b9aba7',
    'src/trading_runtime/runtime.py': '6394a5a4f27196b8b8be50fc63c6734f11b03af946b3c142999c304f168b5986',
    'src/backend/backtest_journal_memory.py': 'fcebb6b414584b0cc596e9e8bcedddfaa1f7fd3d1ab1b68e265f226386ab74ae',
    'src/backend/backtest_typed_projection.py': '05961685789902af33cc963c49287b09b759cc56d82ffbcb8050264e72629501',
    'src/backend/backtest_typed_publisher.py': 'b7081eb57979fe3472a40f37c0954c4febbe6c205203941ee7177c9ca23dc32e',
    'src/trading_runtime/arte_strategy_one_entry_journal.py': '99ea535c7855ad154273ace89ea69d559170649358a45545c1b472dcb8e5a59d',
    'src/trading_runtime/arte_journal_writer.py': '2bfe43dad14887e5684b6e9a0c896886648aec8b3584e7506b7b2d16f3f8a26a',
    'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
    'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
    'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
    'src/trading_runtime/arte_journal_commit_v4.py': 'abb2a67b4fba28029b596897f83972dd4d04997c883f83ad9710db415d5e7d25',
    'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
    'src/backend/replay_run_service.py': '74b73beac9e55608ca668d70ddc21e6dcf7a9a7798868077cd4ee8d60d9427b5',
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
