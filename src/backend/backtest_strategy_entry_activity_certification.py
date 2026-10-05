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
 'src/trading_runtime/arte_entry_activity_v4.py': '9fbd2bcb1ce41a6762ba000439c138318768f472e9502df196831c10d23cf04d',
 'src/backend/backtest_strategy_episode_activity_source.py': '10b172ddb51af4cbef783bf236b5ce2eab01c960cb20526f49ab3962627b4449',
 'src/backend/backtest_strategy_episode_activity_gate.py': '43eaa3e95701f32ab02f5af501ca0e019b03ed91badb8f1bcb62ced3918510b5',
 'src/trading_runtime/strategy_episode_activity_veto.py': 'cb96806e4594bdb2b087b1c12925ee80d296e1313984dce6329f3eee4b911e06',
 'src/backend/backtest_strategy_entry_activity_gate.py': 'a128c7856cbc77854871961ec4d582b5f44b9bdeee517f6dde61360811fd02d4',
 'src/backend/backtest_strategy_entry_activity_source.py': 'db6e9f16dd62c48f98dc5ec8f7bfa40a17f69974bb618097418ec3f246d91681',
 'src/trading_runtime/strategy_thirty_six_release.py': '9a243bfe59b4071497bac22231ac0fb104262af40ff282e4910e6819a8b29308',
 'pipelines/strategy_one/strategy_thirty_six_configuration.py': '4a03259d1ab4a0ae29a2a14672fdef94249cac0b614478817b9f22d8a91d34b6',
 'pipelines/strategy_one/configuration_publisher.py': '355d12708d8779dd79cc7a7994a6731512c3c46ff739a728d08072865b0e5083',
 'src/backend/backtest_strategy_one_configuration.py': '930a7b1c497bdf4fe1cda033f46d1857f497697c075dcb6f0925112b398675bd',
 'src/trading_runtime/strategy_registry.py': '0d20e3a0f38ee2217ae7b0127f862e969620800e47adf3a25d9b92703040f487',
 'src/trading_runtime/numbered_fixed_strategy.py': 'c250b9841885e58d3b690feeb8aa9e52415d4cb2b0fa19e8259a8a603b4a59ea',
 'src/backend/backtest_strategy_certified_price_break.py': 'c55c702d49de21c28873980cb89b04fbf7f576d44d63fc78e649e8ef7c6eaa8e',
 'src/backend/backtest_strategy_one_execution.py': '1e202198a9d406d398cec6abc3c57019410b6a498a1783aa0c8f52cb1ef1d547',
 'src/backend/backtest_strategy_one_coordinator.py': '45a2194f5bfc8930209ee105d7f833f0d58742be292fed74ba622814bdfa2b41',
 'src/backend/backtest_strategy_one_management.py': 'd73db9cf82446d850817e02aade2ba84fa3925b7364dd6d550753b40f0579448',
 'src/trading_runtime/strategy_one_management_snapshot.py': '9dc93010d611c428a0c76e33d677db5d6cd3c645aa49698617ad1f70ed2fd9e2',
 'src/trading_runtime/runtime.py': 'e100caac05294d45df561be0d05e25d2da2a0208a4c1ce18d20dc4d216c890ee',
 'src/backend/backtest_journal_memory.py': '5c26f11e3d64469f119a2d43c90c0c4295f51f09f9de653423f295de105b4b4d',
 'src/backend/backtest_typed_projection.py': 'a9392473c840f7b3b1b4f4d385b86474c20a3272491610db5b6e610e3bb9cb90',
 'src/backend/backtest_typed_publisher.py': 'b93dbcc0ab68998c19674c5b27c500d5295087015c8235afa3ff85dfe249b8fa',
 'src/trading_runtime/arte_strategy_one_entry_journal.py': 'cd5bc28c3542a01bf9b1ef72245a2ffd7a03bf026f9214b1d2a4628e6865312c',
 'src/trading_runtime/arte_journal_writer.py': 'f243eb25533b27c2b3d7a15032767bf6cf966252746ea9ee79dcd311a3168526',
 'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
 'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
 'src/trading_runtime/arte_journal_commit_v4.py': 'f07ed2f11699d904c1074704eb231257ae10acbfcb3aea1e83a16f10cd57f687',
 'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
 'src/backend/replay_run_service.py': '813baf35f0b035ed2076ae3c3d31a18b9fcddbd39a936f35d7334909c9b2f952'}


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
