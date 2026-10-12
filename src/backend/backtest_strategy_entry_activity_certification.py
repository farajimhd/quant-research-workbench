"""Source seal for Strategy36 entry activity, publication and execution routes.

The complete release certificate composes this with the full Strategy35 proof.
This source seal alone establishes neither market coverage nor profitability.
"""
import ast
from src.backend.source_ast_summary import canonical_module_ast_digest
from hashlib import sha256
import json
from pathlib import Path


ENTRY_ACTIVITY_SOURCE_AST = {'scripts/clickhouse/publish_strategy_thirty_six_configuration.py': '9181d082d60d5269e13b69448febc5100b955ff42a9a9acbce0735f28bfd71a9', 'src/trading_runtime/strategy_entry_activity_fade.py': '78219596eedb2c3de41ed0597f4e5bde5efea9d68854645f2786ad53fa5c4b75', 'src/trading_runtime/strategy_entry_activity_witness.py': 'ce4bfd96579271bd7d7124e700c40824e9c870266ff05d9ea10b50bcb4d72bc8', 'src/trading_runtime/arte_entry_activity_v4.py': 'd1925f80bf6309e040d257604a7dcaf890e00e471fb822218cfa1418bb1b52d3', 'src/backend/backtest_strategy_episode_activity_source.py': '0a1bb6874e6e36d377909dfad350b700095637e90cedc4bd4ac426ec9d891444', 'src/backend/backtest_strategy_episode_activity_gate.py': '43eaa3e95701f32ab02f5af501ca0e019b03ed91badb8f1bcb62ced3918510b5', 'src/trading_runtime/strategy_episode_activity_veto.py': 'cb96806e4594bdb2b087b1c12925ee80d296e1313984dce6329f3eee4b911e06', 'src/backend/backtest_strategy_entry_activity_gate.py': 'a128c7856cbc77854871961ec4d582b5f44b9bdeee517f6dde61360811fd02d4', 'src/backend/backtest_strategy_entry_activity_source.py': 'db6e9f16dd62c48f98dc5ec8f7bfa40a17f69974bb618097418ec3f246d91681', 'src/trading_runtime/strategy_thirty_six_release.py': '9a243bfe59b4071497bac22231ac0fb104262af40ff282e4910e6819a8b29308', 'pipelines/strategy_one/strategy_thirty_six_configuration.py': '4a03259d1ab4a0ae29a2a14672fdef94249cac0b614478817b9f22d8a91d34b6', 'pipelines/strategy_one/configuration_publisher.py': 'bebbde79b2ad694092222e96d4e09bd50cb3579295a9ca85bf2fcbcb8ff6dc1f', 'src/backend/backtest_strategy_one_configuration.py': 'e7ac8ac056735d57086db293f47d915a7a987cf0e0e91d428e7117e494399f3f', 'src/trading_runtime/strategy_registry.py': 'a93f5cde8cbf06e0c1af27ff3d8d097f3eb7e869644025dba4fa187f74ebcad4', 'src/trading_runtime/numbered_fixed_strategy.py': '26510f4c0bd533119668301f06f38ccab047e8b835e58ce79efc14a68b7b9326', 'src/backend/backtest_strategy_certified_price_break.py': '8d6a7a26fc783252033f882544312eef55a5407b95e60259640452f9f42891ac', 'src/backend/backtest_strategy_one_execution.py': '1e3731185f9c0d59cb7d13cb15d58501eb446ffffed7ffcb9da013145141309e', 'src/backend/backtest_strategy_one_coordinator.py': '188d9b1853e0ffc7b7fe913728d96c60cb27d800a7cfd6e9b61cca97567c52b4', 'src/backend/backtest_strategy_one_management.py': '8001c4c43ca9775c3b3b45087c55fc36f9786844f6a19b698cc445393e6cc7db', 'src/trading_runtime/strategy_one_management_snapshot.py': '32ecf7eb291ee5953dee6fbbac99649e4b8217d1ac9e51ad4a646438933650a6', 'src/trading_runtime/runtime.py': 'b2d3275f794dd30c8cea4a0e805e98c10534947af78d66a281e2ef02f41888b0', 'src/backend/backtest_journal_memory.py': '37e15b8373ab3a33657bbebd4ac81ca45d1d710aac3cdc2c2093615c7c3ef03d', 'src/backend/backtest_typed_projection.py': '03791c385c40d4dfc0018fdbb00b78e35a9af6705cd0dd133c05d7a79613b1c2', 'src/backend/backtest_typed_publisher.py': 'bd27fa899dde52d917f951cc62f7de79362ff53d7f6d5a839d50a22ca754e824', 'src/trading_runtime/arte_strategy_one_entry_journal.py': '2530ac648d393f7656b19ca1be5bb71cc89a11173b1886437a7f0ec7faae014c', 'src/trading_runtime/arte_journal_writer.py': '7687e8fbeeb7b6eb0f41576a3f6435822773863703e52e10613e23e7c4b8bea5', 'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49', 'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5', 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8', 'src/trading_runtime/arte_journal_commit_v4.py': '8c926caccd7f3e2e4cb5198f2f5db9bad3dc33cb7fe6576de0240c67493d60c2', 'src/trading_runtime/arte_journal_compound_v4.py': 'ab8f12fd5d8c5a4f7679ea7d2000b773465dac2023d2187e85adcf02232fa18c', 'src/backend/replay_run_service.py': '4cacadecbdbd7ea1ddf3be246cf42cb415ff6287f331585dd9783a4708cab5ff', 'src/trading_runtime/entry_momentum_growth.py': '8acec288e9cf7d1c62bf8cb2fa386f82d5db69a63fc9306b90168f8cee3c0b5c', 'src/backend/backtest_declared_initial_momentum.py': '786a12c62a157c092652c8cd7d695fe4f5e8ebfb315c9b8dbab56580fb0a4b81', 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995'}


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
