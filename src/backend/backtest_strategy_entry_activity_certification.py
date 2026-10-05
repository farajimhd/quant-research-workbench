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
 'src/trading_runtime/arte_entry_activity_v4.py': '6d90b449905e58bf317c6353b87067cb4e8c46e66276725de22c44d9ca6a3d14',
 'src/backend/backtest_strategy_episode_activity_source.py': '92c1b9abdeb35321976d00e665a2b7dfd0fae8b7018d497edc771244052dd546',
 'src/backend/backtest_strategy_episode_activity_gate.py': '43eaa3e95701f32ab02f5af501ca0e019b03ed91badb8f1bcb62ced3918510b5',
 'src/trading_runtime/strategy_episode_activity_veto.py': 'cb96806e4594bdb2b087b1c12925ee80d296e1313984dce6329f3eee4b911e06',
 'src/backend/backtest_strategy_entry_activity_gate.py': 'a128c7856cbc77854871961ec4d582b5f44b9bdeee517f6dde61360811fd02d4',
 'src/backend/backtest_strategy_entry_activity_source.py': 'db6e9f16dd62c48f98dc5ec8f7bfa40a17f69974bb618097418ec3f246d91681',
 'src/trading_runtime/strategy_thirty_six_release.py': '9a243bfe59b4071497bac22231ac0fb104262af40ff282e4910e6819a8b29308',
 'pipelines/strategy_one/strategy_thirty_six_configuration.py': '4a03259d1ab4a0ae29a2a14672fdef94249cac0b614478817b9f22d8a91d34b6',
 'pipelines/strategy_one/configuration_publisher.py': '425c546664cdbfc6f01dbd153c90014de1e260c3494033d790621ef81471662c',
 'src/backend/backtest_strategy_one_configuration.py': '0f01eb4464b8e85f9f3c8e227e33f7104c42707aee879a995636230fc49a0c82',
 'src/trading_runtime/strategy_registry.py': '92a48a835437bd4678381a3587299c4e766b2f449bdf0510f43b115fe873fb60',
 'src/trading_runtime/numbered_fixed_strategy.py': 'd09416b3fd0398aaed3bfa3e5d03f2fb5e3dfc8e13a0a216cad0fc1f72a9b773',
 'src/backend/backtest_strategy_certified_price_break.py': '4c5a153c90b99f0d50bb554afcfb3f74b33ee753c0779948ec5f0afac4d96a67',
 'src/backend/backtest_strategy_one_execution.py': '850e6ab6a644eceff98c66f988485845944d615bfbf1918fc54d6665797f61a9',
 'src/backend/backtest_strategy_one_coordinator.py': '5c2088264162096bbaa2ccef35253a1916cf20c676ed92052664fa253fabebc8',
 'src/backend/backtest_strategy_one_management.py': '81b15b9bb8f3feae00e4439f8edbbde89cfc3e9a445695bca70da1903010a05c',
 'src/trading_runtime/strategy_one_management_snapshot.py': 'a0e8acb006050ccde6ca19b6f9543e58d3fa0f080195e9b618bb4faacdd6df18',
 'src/trading_runtime/runtime.py': '338a713370881103586864d0e83b51fcbaa9a55def4309b2213a62d9b095a3c8',
 'src/backend/backtest_journal_memory.py': '6dc2a02199967979d5b93dc8b2af3ed3d9f2c7c6298dd12dd1c1decf5672f48c',
 'src/backend/backtest_typed_projection.py': '05081074d0168f9c6952df453f6c054079a220977a96eb70338e5543a7533d0a',
 'src/backend/backtest_typed_publisher.py': '316ccc51b7a9e93848439fb7eda43158282b2e0d6b59fa45e05c4779494e9233',
 'src/trading_runtime/arte_strategy_one_entry_journal.py': '26acb77c922fe44e274a3f2ff019c2d81b4f1e2a1846ecca666214e8ecf64222',
 'src/trading_runtime/arte_journal_writer.py': 'e9a61cb75de23211d3059ea39e34a276e8736782f77a84f368c94d12208b6e0b',
 'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
 'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
 'src/trading_runtime/arte_journal_commit_v4.py': 'a94e1d8d06b377aa60106036ba42889e43c3631cea1698b562545576c361fbb8',
 'src/trading_runtime/arte_journal_compound_v4.py': 'c270c28e1fa80d4ff0da67d1b2cd64f8c6b402c7db812caa2fb2d8399010d9e7',
 'src/backend/replay_run_service.py': 'b26f537d3d6b03bc0e7ddf6b4218586daf9699c49af56871fdbe753e7b8ad967'}


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
