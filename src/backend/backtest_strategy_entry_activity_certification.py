"""Source seal for Strategy36 entry activity, publication and execution routes.

The complete release certificate composes this with the full Strategy35 proof.
This source seal alone establishes neither market coverage nor profitability.
"""
import ast
from hashlib import sha256
import json
from pathlib import Path


ENTRY_ACTIVITY_SOURCE_AST = {'scripts/clickhouse/publish_strategy_thirty_six_configuration.py': '9181d082d60d5269e13b69448febc5100b955ff42a9a9acbce0735f28bfd71a9',
 'src/trading_runtime/strategy_entry_activity_fade.py': '78219596eedb2c3de41ed0597f4e5bde5efea9d68854645f2786ad53fa5c4b75',
 'src/trading_runtime/strategy_entry_activity_witness.py': 'ce4bfd96579271bd7d7124e700c40824e9c870266ff05d9ea10b50bcb4d72bc8',
 'src/trading_runtime/arte_entry_activity_v4.py': 'defa7526754031d5be77617f0baa0fcb81ef3cc68eab28e9c6a5e842a8c6f7fb',
 'src/backend/backtest_strategy_episode_activity_source.py': 'ab49d8b34628d4cc80e3901112f0a2d97508d58ff3fcea1ffa7fa1f2620c3566',
 'src/backend/backtest_strategy_episode_activity_gate.py': '43eaa3e95701f32ab02f5af501ca0e019b03ed91badb8f1bcb62ced3918510b5',
 'src/trading_runtime/strategy_episode_activity_veto.py': 'cb96806e4594bdb2b087b1c12925ee80d296e1313984dce6329f3eee4b911e06',
 'src/backend/backtest_strategy_entry_activity_gate.py': 'a128c7856cbc77854871961ec4d582b5f44b9bdeee517f6dde61360811fd02d4',
 'src/backend/backtest_strategy_entry_activity_source.py': 'db6e9f16dd62c48f98dc5ec8f7bfa40a17f69974bb618097418ec3f246d91681',
 'src/trading_runtime/strategy_thirty_six_release.py': '9a243bfe59b4071497bac22231ac0fb104262af40ff282e4910e6819a8b29308',
 'pipelines/strategy_one/strategy_thirty_six_configuration.py': '4a03259d1ab4a0ae29a2a14672fdef94249cac0b614478817b9f22d8a91d34b6',
 'pipelines/strategy_one/configuration_publisher.py': '3d5ad0a8e627ca8f2ebdb64d469e5babbb49fd8fb349a1997a861b27185ddb6d',
 'src/backend/backtest_strategy_one_configuration.py': 'e704996f009d645191178125570420d5ff694577eedac9ff731eea0e4f1d8516',
 'src/trading_runtime/strategy_registry.py': '65220011cea8e502d82fee5bbaf58d856e648ceace15dc48e2d0e9020e81f40d',
 'src/trading_runtime/numbered_fixed_strategy.py': 'f9c42ddd6921012fcf3d761a0fd718a8d207df5601d4440b682b40b0769a0784',
 'src/backend/backtest_strategy_certified_price_break.py': '3eef13a9a2baaa85217ccdce64967d1ed4d628573990d524e91a14fd9692eb1c',
 'src/backend/backtest_strategy_one_execution.py': 'a4b08fd1c0ed2bb667bddd221b191986156ca9b230d4ada2578d114cfbd84246',
 'src/backend/backtest_strategy_one_coordinator.py': 'f0ee0c32e7b748bd79c912acc20d12ffa6ba8abda0749f95724d3610e6bd4281',
 'src/backend/backtest_strategy_one_management.py': '54815ec98ebcc11913e48e2f215261100a14860990908ee2c9fc2467aede867c',
 'src/trading_runtime/strategy_one_management_snapshot.py': '6f3644877c90947bf85f443285cfbe4588fe3964f89caa665ecda36af97fac85',
 'src/trading_runtime/runtime.py': 'f9d753e2590b66b9bdfa55a7d6d083dc617dd790d8732e1030a5e43d4ad72acb',
 'src/backend/backtest_journal_memory.py': '38697454c74a76fde23724929cf702f36ca9d55e013e0074b5a832891d4878f4',
 'src/backend/backtest_typed_projection.py': '9270813823b23a636a362a82938882eb9672a730d263be50b73de44af25d4336',
 'src/backend/backtest_typed_publisher.py': '254e2c609b79f8e6bd2a6d109b4887a4ca7028c7dc89bfd3380453d6b7c72b9d',
 'src/trading_runtime/arte_strategy_one_entry_journal.py': '8f873b171521ea6787b3ac62e5b080b9dfe1008bc43607e38d2c029caee3d3e9',
 'src/trading_runtime/arte_journal_writer.py': '9f57286328c1ece2f5a58e0f316f2d761dd616f4bad586d4932604f01d158c1e',
 'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
 'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
 'src/trading_runtime/arte_journal_commit_v4.py': '7916f90293d5493f0ac9dac3532ee616ec82e27f15d81f4d0f086215cc8b890f',
 'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
 'src/backend/replay_run_service.py': '0c5236fa72b87a36dd1675c28966d0431a042590e032fc72f3f055e713fa576d'}


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
