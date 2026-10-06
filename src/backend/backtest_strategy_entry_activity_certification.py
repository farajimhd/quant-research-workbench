"""Source seal for Strategy36 entry activity, publication and execution routes.

The complete release certificate composes this with the full Strategy35 proof.
This source seal alone establishes neither market coverage nor profitability.
"""
import ast
from src.backend.source_ast_summary import canonical_module_ast_digest
from hashlib import sha256
import json
from pathlib import Path


ENTRY_ACTIVITY_SOURCE_AST = {'scripts/clickhouse/publish_strategy_thirty_six_configuration.py': '9181d082d60d5269e13b69448febc5100b955ff42a9a9acbce0735f28bfd71a9', 'src/trading_runtime/strategy_entry_activity_fade.py': '78219596eedb2c3de41ed0597f4e5bde5efea9d68854645f2786ad53fa5c4b75', 'src/trading_runtime/strategy_entry_activity_witness.py': 'ce4bfd96579271bd7d7124e700c40824e9c870266ff05d9ea10b50bcb4d72bc8', 'src/trading_runtime/arte_entry_activity_v4.py': '95b32e75b49992cb38e0569e1576e093841f46eb3b8defd37e5ec4d0e8494b92', 'src/backend/backtest_strategy_episode_activity_source.py': '2d4fa40c46a866be83328ce7443a350655620bf9c4b4a82044dc45ee7441c8c0', 'src/backend/backtest_strategy_episode_activity_gate.py': '43eaa3e95701f32ab02f5af501ca0e019b03ed91badb8f1bcb62ced3918510b5', 'src/trading_runtime/strategy_episode_activity_veto.py': 'cb96806e4594bdb2b087b1c12925ee80d296e1313984dce6329f3eee4b911e06', 'src/backend/backtest_strategy_entry_activity_gate.py': 'a128c7856cbc77854871961ec4d582b5f44b9bdeee517f6dde61360811fd02d4', 'src/backend/backtest_strategy_entry_activity_source.py': 'db6e9f16dd62c48f98dc5ec8f7bfa40a17f69974bb618097418ec3f246d91681', 'src/trading_runtime/strategy_thirty_six_release.py': '9a243bfe59b4071497bac22231ac0fb104262af40ff282e4910e6819a8b29308', 'pipelines/strategy_one/strategy_thirty_six_configuration.py': '4a03259d1ab4a0ae29a2a14672fdef94249cac0b614478817b9f22d8a91d34b6', 'pipelines/strategy_one/configuration_publisher.py': 'd6eedccd686e0d26b39153cf8cb07d753f9fae1f77818cf367b0e30ef6570de1', 'src/backend/backtest_strategy_one_configuration.py': '7fb2771427ab2bb7e629d8d20b7208c1af7068689fb92eb16fed5a6907bd0ccd', 'src/trading_runtime/strategy_registry.py': '708960fc0d38a859ae97d58f7feac74aa8c8087671b826d3acae0eb4d15a0a9f', 'src/trading_runtime/numbered_fixed_strategy.py': '3292fce6d35bcf81fc3ce284d47ce133b49dbc528c902178f15f40d27cc3bc46', 'src/backend/backtest_strategy_certified_price_break.py': 'e1fa545fbbd95b4784e4cf2e0ef320276a4ff525683f680ddea6fc0ccd4c1a38', 'src/backend/backtest_strategy_one_execution.py': '637da264cea91bcf316473704933e6b8a0932b43509a6d5a3ffb9809451a9a93', 'src/backend/backtest_strategy_one_coordinator.py': '7d40c9a3d75872e1ff263729eb578cf9860e913bae3863ec8889ca999e5df334', 'src/backend/backtest_strategy_one_management.py': '96c4bc9c6ac5e3041bba14bb382565395d4431549f1e3de98dcb7c8c582c1e1e', 'src/trading_runtime/strategy_one_management_snapshot.py': 'c6bc24067dac5501499bcf1bdf92d84b45361776ee43bd104d5e40c95e2d1f05', 'src/trading_runtime/runtime.py': '57af631c859aa491b604830bb552f47abdc3e50e8a94d2b7fbeec234133fa465', 'src/backend/backtest_journal_memory.py': '6c31e1a22ea4ae131363e4a7b630f0edeb0ab14bb0c0c641cd4ecb8175ba2864', 'src/backend/backtest_typed_projection.py': '942a03a57b59c9371c1fb2f62310ab31706d56f1e2a5f2326ba3efb745e9908c', 'src/backend/backtest_typed_publisher.py': '24e615607412b1a477f405a4188c24db67b69031a74965de0fbc189ee369c218', 'src/trading_runtime/arte_strategy_one_entry_journal.py': '45dc2b6abf7f528fbf1e056c5249a78eb1b52813f051a732512712abc5993856', 'src/trading_runtime/arte_journal_writer.py': '3040cb3e96f6fc0448ce7ab039d1dd08fbaa37126777e9954c4a5b33aac13a31', 'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49', 'src/trading_runtime/arte_typed_insert_dispatch.py': '9d5cc6c222a11237b11bf07a25692ea15cf008ed9e15fc1146062d237e12a639', 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8', 'src/trading_runtime/arte_journal_commit_v4.py': 'b554846e3a213c17818c6e89cfb3baee1f0738834caf3dc0d0d3b526b16d0e22', 'src/trading_runtime/arte_journal_compound_v4.py': 'c270c28e1fa80d4ff0da67d1b2cd64f8c6b402c7db812caa2fb2d8399010d9e7', 'src/backend/replay_run_service.py': '1871d07a8d40f07def8accc0811ca228320d49940a9a122f8f71c496e5831440', 'src/trading_runtime/entry_momentum_growth.py': '8acec288e9cf7d1c62bf8cb2fa386f82d5db69a63fc9306b90168f8cee3c0b5c', 'src/backend/backtest_declared_initial_momentum.py': '786a12c62a157c092652c8cd7d695fe4f5e8ebfb315c9b8dbab56580fb0a4b81', 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995', 'src/trading_runtime/squeeze_ladder_geometry.py': '34658cac7dba475b19ca3963bbcad1397a88f5f86e4133664947c9e656671e0d'}


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
            actual = canonical_module_ast_digest(source)
        except SyntaxError as exc:
            raise ValueError('Entry activity source cannot be parsed: ' + relative) from exc
        if actual != expected:
            raise ValueError('Entry activity source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
