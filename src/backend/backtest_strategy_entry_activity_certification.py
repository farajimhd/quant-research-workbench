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
 'src/trading_runtime/arte_entry_activity_v4.py': '1ecbca6580086078aa1c9364f65d323961173a61df0b7bae6aa631a8f93812eb',
 'src/backend/backtest_strategy_episode_activity_source.py': '157dee72c0c99d75fdc40ee3af33a34c034ebe53e8688236a622dbf4ea07d010',
 'src/backend/backtest_strategy_episode_activity_gate.py': '43eaa3e95701f32ab02f5af501ca0e019b03ed91badb8f1bcb62ced3918510b5',
 'src/trading_runtime/strategy_episode_activity_veto.py': 'cb96806e4594bdb2b087b1c12925ee80d296e1313984dce6329f3eee4b911e06',
 'src/backend/backtest_strategy_entry_activity_gate.py': 'a128c7856cbc77854871961ec4d582b5f44b9bdeee517f6dde61360811fd02d4',
 'src/backend/backtest_strategy_entry_activity_source.py': 'db6e9f16dd62c48f98dc5ec8f7bfa40a17f69974bb618097418ec3f246d91681',
 'src/trading_runtime/strategy_thirty_six_release.py': '9a243bfe59b4071497bac22231ac0fb104262af40ff282e4910e6819a8b29308',
 'pipelines/strategy_one/strategy_thirty_six_configuration.py': '4a03259d1ab4a0ae29a2a14672fdef94249cac0b614478817b9f22d8a91d34b6',
 'pipelines/strategy_one/configuration_publisher.py': '2bda267ca9ea242d6be5d30460d0d66c37b3bf6935867343a201b8f20d03f00b',
 'src/backend/backtest_strategy_one_configuration.py': 'd5a2bc061571e09ae55ea611e7e99f5e608f9ab9dec7e350d2742d7fca3224c9',
 'src/trading_runtime/strategy_registry.py': '5d67e5a268fb2b07824c733033a9907237ba05dc962289cc51f3262333dc1efe',
 'src/trading_runtime/numbered_fixed_strategy.py': 'e77afb232629caaf7cd736f3e42ffda9e71690003fd3943b690b142742c55d6f',
 'src/backend/backtest_strategy_certified_price_break.py': 'c11b20c8ed88b2d3350da6a494d5209176069507ae2f6a47a4529f9baf50b6c6',
 'src/backend/backtest_strategy_one_execution.py': '4dfbb938081cdcdd651bd2a148f73e498338df141a20f162bc5507e0e3c77852',
 'src/backend/backtest_strategy_one_coordinator.py': 'e3f87d0517f559cdf90de7407092d3aad20e0a622cb551291257dc28f791b481',
 'src/backend/backtest_strategy_one_management.py': '5eba2fbe247ced68f8d62ff653ceb68fa1486e8e0dbdf00140c88f32604b10c5',
 'src/trading_runtime/strategy_one_management_snapshot.py': '76102fb098cbd89d47ec86849597aae7b0fc65f79263ace8455d30d79c43eb73',
 'src/trading_runtime/runtime.py': 'c932991d4183d8dd42999157e8370d5c0e57ce411736e8de45ce1f64fc740a2e',
 'src/backend/backtest_journal_memory.py': '717a5e4a4f5a31806282b3f7b37ba88c04e3862cd8413d49ff0f1bf79bc1277e',
 'src/backend/backtest_typed_projection.py': '22ab5cb4116e3509b27c882a6234adb16e38e9853146014d9cb4b3eca4bdba68',
 'src/backend/backtest_typed_publisher.py': 'a211e08727e013d4429582960c2e9c60a9d82348d4bb2248aaf2a189ea864be6',
 'src/trading_runtime/arte_strategy_one_entry_journal.py': 'f53c55086ff39bbd10e99568c1b50eea649dad2850888b8bf68d0bebf6a35e90',
 'src/trading_runtime/arte_journal_writer.py': '6c7b13c39de95592840b2224c202bb0b00e996076612bd6514e10b6c0d93204f',
 'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
 'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
 'src/trading_runtime/arte_journal_commit_v4.py': 'c1f3fa36c9e782ba6badb204b85ffe58acdfe044e40f52228c22b2e6aef5c3eb',
 'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
 'src/backend/replay_run_service.py': 'a98a1067c73fb30daa6c17dfb4c07aadf9fb253ad595da32af8f373173fdc215'}


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
