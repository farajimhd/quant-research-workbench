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
 'src/trading_runtime/arte_entry_activity_v4.py': '2ccca4ffff16da08af0c021c74cb408847255cb81683f4ae122573351de5e6e3',
 'src/backend/backtest_strategy_episode_activity_source.py': '5c4c5d98d473c08831aa1c28cae7f386459b51bbcb824cc4f518bd8e6ef8b12d',
 'src/backend/backtest_strategy_episode_activity_gate.py': '43eaa3e95701f32ab02f5af501ca0e019b03ed91badb8f1bcb62ced3918510b5',
 'src/trading_runtime/strategy_episode_activity_veto.py': 'cb96806e4594bdb2b087b1c12925ee80d296e1313984dce6329f3eee4b911e06',
 'src/backend/backtest_strategy_entry_activity_gate.py': 'a128c7856cbc77854871961ec4d582b5f44b9bdeee517f6dde61360811fd02d4',
 'src/backend/backtest_strategy_entry_activity_source.py': 'db6e9f16dd62c48f98dc5ec8f7bfa40a17f69974bb618097418ec3f246d91681',
 'src/trading_runtime/strategy_thirty_six_release.py': '9a243bfe59b4071497bac22231ac0fb104262af40ff282e4910e6819a8b29308',
 'pipelines/strategy_one/strategy_thirty_six_configuration.py': '4a03259d1ab4a0ae29a2a14672fdef94249cac0b614478817b9f22d8a91d34b6',
 'pipelines/strategy_one/configuration_publisher.py': '3c03d2221a858f87e2cdaaf279668e32450a9586c68600121af74b3638fa0c13',
 'src/backend/backtest_strategy_one_configuration.py': '2803e66aaeea18c8ef6cd36d9eb7db94c8c69c8de1e867090f7d4d6477df69dd',
 'src/trading_runtime/strategy_registry.py': 'c7c4bda74685a3b6197440192e868151eaf4b88d0b35af165380e85f66b21ebd',
 'src/trading_runtime/numbered_fixed_strategy.py': 'aa5b7d49f4b6a0f6a83143c63fd2875fc21bebd202815d652a9d59ed4e6671c1',
 'src/backend/backtest_strategy_certified_price_break.py': 'c98c595af1dd70906fdb99fc7210ffc1533d86ec31aa7c50606acdab78057fda',
 'src/backend/backtest_strategy_one_execution.py': '141e77c828cb552ade7a82fb78c665739b3ca699e8bf1449f8ec642c7b82442b',
 'src/backend/backtest_strategy_one_coordinator.py': 'bcb57fe0c67bb308c5d7b00f0ba73a4e10c8532dc1096ee61be217f06e662cc3',
 'src/backend/backtest_strategy_one_management.py': 'f1eaa31e0b38d3ab80759da1e78a23b1f239646ffe4d3fd7a4e3c2dacdd5bb08',
 'src/trading_runtime/strategy_one_management_snapshot.py': '3cf52e52b1030e5e4d197a14f84493cbd6534a7d7f99537b37a3f4c644f3086d',
 'src/trading_runtime/runtime.py': '8e759caac1b144be2ddbb1bb9cd42cf0371585b8cd33eec74434f03111199df2',
 'src/backend/backtest_journal_memory.py': '1562f6f509f4e2077c5b64e4ec002e04bb2323569dc9136dbde216249e28ff1b',
 'src/backend/backtest_typed_projection.py': '9c79b7428940e991bd46d675f1cb2d72ed3985a897bbedc6f640bebb9202c11d',
 'src/backend/backtest_typed_publisher.py': 'ccf5913cf4dc5e4772aab174267eb68c0e45bdcb75940f2fefbd2132e35ef932',
 'src/trading_runtime/arte_strategy_one_entry_journal.py': '9b841984ba4e2e3369e43c21893ef44d6ba7273e7f31ed3dc22374935706bde8',
 'src/trading_runtime/arte_journal_writer.py': 'e85109922720b079e6b5ea8fbe8cc24b16cd733e4a12a9e0dc15ffe82e78d0f3',
 'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
 'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
 'src/trading_runtime/arte_journal_commit_v4.py': 'ab86d9ff355a01f980e8d06153cd8a0f366a560374a378bc8c06a46e6475d354',
 'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
 'src/backend/replay_run_service.py': '974f969a268e07be9aff6f973d70bebb7401ad6a5c36d3742bbb58c63254ac25'}


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
