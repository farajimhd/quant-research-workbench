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
 'src/trading_runtime/arte_entry_activity_v4.py': 'baa2f4ce4dcb37d23d720363e18b21790c7fb545d254544ad723a6db4a11601e',
 'src/backend/backtest_strategy_episode_activity_source.py': '0830cea4da3228700c7372a599d1b5af1856e0cccf797be6f0b0ed591385f169',
 'src/backend/backtest_strategy_episode_activity_gate.py': '43eaa3e95701f32ab02f5af501ca0e019b03ed91badb8f1bcb62ced3918510b5',
 'src/trading_runtime/strategy_episode_activity_veto.py': 'cb96806e4594bdb2b087b1c12925ee80d296e1313984dce6329f3eee4b911e06',
 'src/backend/backtest_strategy_entry_activity_gate.py': 'a128c7856cbc77854871961ec4d582b5f44b9bdeee517f6dde61360811fd02d4',
 'src/backend/backtest_strategy_entry_activity_source.py': 'db6e9f16dd62c48f98dc5ec8f7bfa40a17f69974bb618097418ec3f246d91681',
 'src/trading_runtime/strategy_thirty_six_release.py': '9a243bfe59b4071497bac22231ac0fb104262af40ff282e4910e6819a8b29308',
 'pipelines/strategy_one/strategy_thirty_six_configuration.py': '4a03259d1ab4a0ae29a2a14672fdef94249cac0b614478817b9f22d8a91d34b6',
 'pipelines/strategy_one/configuration_publisher.py': 'b6a7ce44ff0729b0eac8aa0dd0f2eac355661b9c1a76f1d94f159be28c5813d1',
 'src/backend/backtest_strategy_one_configuration.py': '9fc30e92b34bb1690625dd63a3cd18bd0ddfb89497fd1df33c67bfec804fcb21',
 'src/trading_runtime/strategy_registry.py': '56a9a7e178c7c062e7948ba495621e250572c15bd5063fff960e8c41a5ead0c8',
 'src/trading_runtime/numbered_fixed_strategy.py': '59e01acb2138459dfa819c6853b8096688c15d9307ed03e2b1e841ebe01042ab',
 'src/backend/backtest_strategy_certified_price_break.py': 'c65262cd6ed9af7c71527994d66c8cf38dc56dbd3600973804c22fdb4caa9627',
 'src/backend/backtest_strategy_one_execution.py': '37eaef4da4a0f6503d5d98c33ab560df14b975f1ae571249171f55ac1631fab0',
 'src/backend/backtest_strategy_one_coordinator.py': '9549d626e2220efae49073992191cbf340630e28d971b5014cf5d7b10ec089cb',
 'src/backend/backtest_strategy_one_management.py': 'c4d3722667eb5024f09086f5e65ce258361028993a65eef2de6af69ff4f645a9',
 'src/trading_runtime/strategy_one_management_snapshot.py': 'cacbd10bd33f921f74cfe31926a8a51ab53f6f98c011a53444dd10b901a3104d',
 'src/trading_runtime/runtime.py': '4dfba1e0baa9cebefea0d1625062debaa1b477ada24845a2adcdd10d885ffa30',
 'src/backend/backtest_journal_memory.py': '5bf8e6c016b00b7664f24c26ab35f5e977d71595548baa529fef3a0de42477f6',
 'src/backend/backtest_typed_projection.py': 'dad68574a6ca57515f41fa37898cf6023ea764a3bb93cd6ceb4b09a14503b20b',
 'src/backend/backtest_typed_publisher.py': '7030b248806e7d009f0a78bcb1abd47faec64444fd1779367d4993cbddd74bc7',
 'src/trading_runtime/arte_strategy_one_entry_journal.py': '1e46fe8243c97d4aa09ca60f219005b5cac6c01cd404329647f4c881a0ff9ae0',
 'src/trading_runtime/arte_journal_writer.py': '0057d79ac2d010ee112413c323404ffa8633e6b3879f90f892e6a92800c5c77a',
 'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
 'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
 'src/trading_runtime/arte_journal_commit_v4.py': '21eeb5914ff32bf6f2efa95fe1c3a3bb8deef581be6d9d350a951f70f20bcbc7',
 'src/trading_runtime/arte_journal_compound_v4.py': 'c270c28e1fa80d4ff0da67d1b2cd64f8c6b402c7db812caa2fb2d8399010d9e7',
 'src/backend/replay_run_service.py': 'c87e6edf96b94a7f86c620f01ce647d128e57148de554d6b0eab5173f2e77178'}


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
