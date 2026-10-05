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
    'src/trading_runtime/arte_entry_activity_v4.py': '31945cf27cf094a8e4fdad2f77ad6a2342dc3f086c70f3b72e1003b5e7b774e4',
    'src/backend/backtest_strategy_episode_activity_source.py': 'ae97ab90b85660d5747bb57e9444bf17751b55e1577948d1160f234aae3efbfa',
    'src/backend/backtest_strategy_episode_activity_gate.py': '43eaa3e95701f32ab02f5af501ca0e019b03ed91badb8f1bcb62ced3918510b5',
    'src/trading_runtime/strategy_episode_activity_veto.py': 'cb96806e4594bdb2b087b1c12925ee80d296e1313984dce6329f3eee4b911e06',
    'src/backend/backtest_strategy_entry_activity_gate.py': 'a128c7856cbc77854871961ec4d582b5f44b9bdeee517f6dde61360811fd02d4',
    'src/backend/backtest_strategy_entry_activity_source.py': 'db6e9f16dd62c48f98dc5ec8f7bfa40a17f69974bb618097418ec3f246d91681',
    'src/trading_runtime/strategy_thirty_six_release.py': '9a243bfe59b4071497bac22231ac0fb104262af40ff282e4910e6819a8b29308',
    'pipelines/strategy_one/strategy_thirty_six_configuration.py': '4a03259d1ab4a0ae29a2a14672fdef94249cac0b614478817b9f22d8a91d34b6',
    'pipelines/strategy_one/configuration_publisher.py': '8eb314925f85fa5edebd8110c73f5a57868cdf100e3c150f607470f26cf2ce04',
    'src/backend/backtest_strategy_one_configuration.py': '3de29edec7014e49d1501434c586ba5b5789ce5a05d7a452c1d76ca11db558b2',
    'src/trading_runtime/strategy_registry.py': '16ea81cdccfe24ba7be1e5533b5904f9e3d389279031a09a4f34688435b22ffc',
    'src/trading_runtime/numbered_fixed_strategy.py': '9b67056857a3f0996238ec5d5eac6822056de1efa389eacce80ff1620a768175',
    'src/backend/backtest_strategy_certified_price_break.py': 'e4463985da453619128e9690cef4072917cb62662e9f49e90bd09a4964dc49ff',
    'src/backend/backtest_strategy_one_execution.py': '00fad0f28a4f4983a292f2b80faa6f5eea3a4e7299fd83d5844bf02326036ffb',
    'src/backend/backtest_strategy_one_coordinator.py': '6675e437653e32fc691e6402875e5612a6dcfe65d8ce9e41bda64b249a586890',
    'src/backend/backtest_strategy_one_management.py': '3d8c3964c20fed559e753609f9fcbdfaa8e9456fed09cc2f2e5c5e4aabf94446',
    'src/trading_runtime/strategy_one_management_snapshot.py': 'c824fad3b87d8dceae518a6e11d5747dccb24e1a170bceea166904754cabe6b9',
    'src/trading_runtime/runtime.py': 'bb0208c12770110b7461144fff08af3cab5890e4b6176b20af1030e8197ded7f',
    'src/backend/backtest_journal_memory.py': 'a163faa0678e524fce4f5e10e9eb98165a59e3f6c31955f770c2be156d4144e9',
    'src/backend/backtest_typed_projection.py': 'c3dc22f7b20f0e3bab12297a00ad009db83957b7ca1617db5d6863a7a868f444',
    'src/backend/backtest_typed_publisher.py': '835a3b666e00f437b002d846ec9d486b1e298db48ed5aedd8ebb5a650f2e544b',
    'src/trading_runtime/arte_strategy_one_entry_journal.py': 'f680c2723fe597c11fed56db4f1c241e1d7ae065fdae51b4cd9299c52937b99d',
    'src/trading_runtime/arte_journal_writer.py': 'dc286798a43e16b3e2680f48661c5285dba3a091d446d3a98fa8d9746f7536f8',
    'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
    'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
    'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
    'src/trading_runtime/arte_journal_commit_v4.py': '31601f940a7a1bdf844895cecb667cfc9f22a1cd7543f016d3d4fe9a4e113f22',
    'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
    'src/backend/replay_run_service.py': '7a8c2c197736878d61717b591b4fcf599ad05f071ab0834b85ea3cc13f0c3c53',
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
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Entry activity source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Entry activity source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
