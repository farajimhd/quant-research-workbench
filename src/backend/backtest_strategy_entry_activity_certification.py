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
 'src/trading_runtime/arte_entry_activity_v4.py': 'a0dddefe7d64b2486f9dc5c22aa88981ef509601e03e1e4543a1b338bb1a491a',
 'src/backend/backtest_strategy_episode_activity_source.py': '9f87032a7764347d25a3c86db2e62ce10adeb0a7debda994432c34212caa9465',
 'src/backend/backtest_strategy_episode_activity_gate.py': '43eaa3e95701f32ab02f5af501ca0e019b03ed91badb8f1bcb62ced3918510b5',
 'src/trading_runtime/strategy_episode_activity_veto.py': 'cb96806e4594bdb2b087b1c12925ee80d296e1313984dce6329f3eee4b911e06',
 'src/backend/backtest_strategy_entry_activity_gate.py': 'a128c7856cbc77854871961ec4d582b5f44b9bdeee517f6dde61360811fd02d4',
 'src/backend/backtest_strategy_entry_activity_source.py': 'db6e9f16dd62c48f98dc5ec8f7bfa40a17f69974bb618097418ec3f246d91681',
 'src/trading_runtime/strategy_thirty_six_release.py': '9a243bfe59b4071497bac22231ac0fb104262af40ff282e4910e6819a8b29308',
 'pipelines/strategy_one/strategy_thirty_six_configuration.py': '4a03259d1ab4a0ae29a2a14672fdef94249cac0b614478817b9f22d8a91d34b6',
 'pipelines/strategy_one/configuration_publisher.py': '7d18b48bcab46736081384bf1ce994f4e9edda501c22254e4c12d755954f7874',
 'src/backend/backtest_strategy_one_configuration.py': 'e13e33f96c71278223ab8babc839052f3659f2e3bbccbd8beb79ac61deef24ca',
 'src/trading_runtime/strategy_registry.py': '4d08aee98475473a2772c2619cf69b7232e22a7dbb2373f7a50ead40ab8699d8',
 'src/trading_runtime/numbered_fixed_strategy.py': '0f6a62a9c7096f278caacc6861fff149978497779e4f72f0140d8466ee7389ea',
 'src/backend/backtest_strategy_certified_price_break.py': 'f3a28484ef0937b5f3e585c5ee8a6b4c12373c3082c362c9227ad7abf652d9f0',
 'src/backend/backtest_strategy_one_execution.py': '6891230fd947b41e708aa458ce6563202135f867dc12a572e0a9f5f40f01b83c',
 'src/backend/backtest_strategy_one_coordinator.py': '79063bebc5d92af4e1704c6944fbbe2b81a2ba3780d8dc8de37dc21688bacfe1',
 'src/backend/backtest_strategy_one_management.py': '43579b3257669e1f2dbee179bccd8cc0618b0c2eec0c0ce914104a5609d417f6',
 'src/trading_runtime/strategy_one_management_snapshot.py': '4a114cc9d2a7f82000b518b1e89ec835cc0c9b270ed67f45ec315a462ba4555a',
 'src/trading_runtime/runtime.py': '09e06f9cfa3bc9d6fbe3e590b04fe2787947a3e6be835839e3d1b1d10c38831f',
 'src/backend/backtest_journal_memory.py': 'da93c31c96151883cd04196e956ab28319ac0943c2353b5b717719380d0606a2',
 'src/backend/backtest_typed_projection.py': '1fb9b10d98019e8a2291a8ef5a759fc4aee58995da63dc64c5657878f4a0d759',
 'src/backend/backtest_typed_publisher.py': '7134af423e8b564586532df8993c731761541c5d31883dd35c88d2525f6fcf04',
 'src/trading_runtime/arte_strategy_one_entry_journal.py': '6b962b8a200288cbf6094b2339033ae3c31c67c42c03dc1f9edb1d1bb43304fe',
 'src/trading_runtime/arte_journal_writer.py': 'c9fd82b8dd967ee08771491b4dad695a82db9dc010326227a3a6e528f837c1c4',
 'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
 'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
 'src/trading_runtime/arte_journal_commit_v4.py': '658edbb634d13c32bdf976d7098ba5f503f3d691579c820c29a64b74e66752c8',
 'src/trading_runtime/arte_journal_compound_v4.py': 'c270c28e1fa80d4ff0da67d1b2cd64f8c6b402c7db812caa2fb2d8399010d9e7',
 'src/backend/replay_run_service.py': '4291f16739779c5bd880a1049bf25f736bb32799b321770b6526e41dbe6c7035'}


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
