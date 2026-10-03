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
    'src/trading_runtime/arte_entry_activity_v4.py': 'f39af7f37e4333fb87226f267369b65f15cddc13e64a51875e25575ad037d2e1',
    'src/backend/backtest_strategy_episode_activity_source.py': '934c572ecf2a3d5f88d274f238f1b51a2dab2886ab28d3299c779437d2c545aa',
    'src/backend/backtest_strategy_episode_activity_gate.py': '43eaa3e95701f32ab02f5af501ca0e019b03ed91badb8f1bcb62ced3918510b5',
    'src/trading_runtime/strategy_episode_activity_veto.py': 'cb96806e4594bdb2b087b1c12925ee80d296e1313984dce6329f3eee4b911e06',
    'src/backend/backtest_strategy_entry_activity_gate.py': 'a128c7856cbc77854871961ec4d582b5f44b9bdeee517f6dde61360811fd02d4',
    'src/backend/backtest_strategy_entry_activity_source.py': 'db6e9f16dd62c48f98dc5ec8f7bfa40a17f69974bb618097418ec3f246d91681',
    'src/trading_runtime/strategy_thirty_six_release.py': '9a243bfe59b4071497bac22231ac0fb104262af40ff282e4910e6819a8b29308',
    'pipelines/strategy_one/strategy_thirty_six_configuration.py': '4a03259d1ab4a0ae29a2a14672fdef94249cac0b614478817b9f22d8a91d34b6',
    'pipelines/strategy_one/configuration_publisher.py': '571e90ade71025ce0fb71cea99895c0e20145b12dbefc1b2398efbd7a2601cbc',
    'src/backend/backtest_strategy_one_configuration.py': 'd75eed6ac5a3404930474e524bff5f905ee4dc42cae83c6d27444a50f56b45df',
    'src/trading_runtime/strategy_registry.py': 'd6b06838efb9d86db9e8ccf258c0d434913a28a583cae0dd7bfa9dfbd4bfe842',
    'src/trading_runtime/numbered_fixed_strategy.py': '4572b8612247111a011c396f39ef14d879dca99b6988a81b0cc5a60f9103e861',
    'src/backend/backtest_strategy_certified_price_break.py': 'c29fc5672ee4fba3d38658e917fe582ba043e83c1df04d10036f48418dff72f5',
    'src/backend/backtest_strategy_one_execution.py': 'feffe123c7935ee493cdba181474d56d078a3f4f525b980a1a3151f538c96d04',
    'src/backend/backtest_strategy_one_coordinator.py': 'b3da0885371cca61702e03110614a7fc1701edfa779a577ea1ff35ef75881270',
    'src/backend/backtest_strategy_one_management.py': '898697d401e8391cc7b6a83f19044365d9e49535ec808239f8ef4549942b2959',
    'src/trading_runtime/strategy_one_management_snapshot.py': '47fa9cda2196e01c1e30840fd2944d409700fbd71c240caeff161c0f1f32f54c',
    'src/trading_runtime/runtime.py': 'c159dbf330294abaae988a5e59557e9dd81563690becafcd5646c64f786eadba',
    'src/backend/backtest_journal_memory.py': '04e99b3a6699dee25a79546ac3062cfbda4291a50a13c71cb7b3a841962b12b7',
    'src/backend/backtest_typed_projection.py': 'ef1af5aac44be18d27c909fcd7bc91bd6bbab14fa2f9da18f47b61dcad95a432',
    'src/backend/backtest_typed_publisher.py': '3570e265f99450c7993d50097bc7fa354eff8021355f6fdf41d1c556c44ee664',
    'src/trading_runtime/arte_strategy_one_entry_journal.py': '46c4915dc9f07a4abe7e36954384294f5c12ca0480828071030a7f40070b961a',
    'src/trading_runtime/arte_journal_writer.py': 'abff555cd4be1eb812ebac563f6910772f00a18c98ab0d7c537c3e405b12b83e',
    'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
    'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
    'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
    'src/trading_runtime/arte_journal_commit_v4.py': 'dd2cd07dacfda6e45e689c8aead1048163ccb5c11b9cdb3bccca986f446410b7',
    'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
    'src/backend/replay_run_service.py': '61b65108aa9636197b6428acfb8a63dceb8dfb9cecca46374fbb1def864afbf4',
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
            from .backtest_historical_strategy_projection import historical_strategy_tree
            tree = historical_strategy_tree(ast.parse(source), relative)
        except SyntaxError as exc:
            raise ValueError('Entry activity source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Entry activity source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
