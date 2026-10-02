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
    'src/trading_runtime/arte_entry_activity_v4.py': '48444688e3f5f03d0908be0232297cf37c80cc86881b58688d1a9a162a1bc707',
    'src/backend/backtest_strategy_episode_activity_source.py': '5f711f0d4c9e4f3e67dad2cf8f5bf2f5f19892e9cc08ef4a73c878451c28bca7',
    'src/backend/backtest_strategy_episode_activity_gate.py': '43eaa3e95701f32ab02f5af501ca0e019b03ed91badb8f1bcb62ced3918510b5',
    'src/trading_runtime/strategy_episode_activity_veto.py': 'cb96806e4594bdb2b087b1c12925ee80d296e1313984dce6329f3eee4b911e06',
    'src/backend/backtest_strategy_entry_activity_gate.py': 'a128c7856cbc77854871961ec4d582b5f44b9bdeee517f6dde61360811fd02d4',
    'src/backend/backtest_strategy_entry_activity_source.py': 'db6e9f16dd62c48f98dc5ec8f7bfa40a17f69974bb618097418ec3f246d91681',
    'src/trading_runtime/strategy_thirty_six_release.py': '9a243bfe59b4071497bac22231ac0fb104262af40ff282e4910e6819a8b29308',
    'pipelines/strategy_one/strategy_thirty_six_configuration.py': '4a03259d1ab4a0ae29a2a14672fdef94249cac0b614478817b9f22d8a91d34b6',
    'pipelines/strategy_one/configuration_publisher.py': 'dce2f6e9d03186d6ce38a4b61fc4d75b36ed6b25c70f60eefce632415b6daa86',
    'src/backend/backtest_strategy_one_configuration.py': '325be0505bb7b4ee6b8b9bd0d251134cbd3d0b9e42035c27684bf29569bcf31b',
    'src/trading_runtime/strategy_registry.py': '679630a96826f88d684dbc0b87783dfbfa82f32aa35ab31af3f0535362f99677',
    'src/trading_runtime/numbered_fixed_strategy.py': 'af28912cdc86a6d01e8591b511c9bf6a74088049bfd4bdda69e3cca0744dabc4',
    'src/backend/backtest_strategy_certified_price_break.py': '3ff49d39f1667f5d8e84de70a9b30b286f785a987408deaa050941dff6857bd8',
    'src/backend/backtest_strategy_one_execution.py': '682fc5080071a210aaca1b6f91fdd2cebf8c4fe11f05c4e3aa5c94f7ba2809cb',
    'src/backend/backtest_strategy_one_coordinator.py': '7963a7e094838be998606b5a088b046cfaa8066e3ee0602cd9bb2bd9f890da01',
    'src/backend/backtest_strategy_one_management.py': '001e1108165718d1df344072ee7dbcaef6d08fbf3fdc2d2b6bcec9e6117dd0c6',
    'src/trading_runtime/strategy_one_management_snapshot.py': '25698c2a64d42a173a5e4e5f5a5433c3bedccb0f825730a16b04a6b3f515f844',
    'src/trading_runtime/runtime.py': 'ffb717541376b3d8e6809a2d8c0e24fe3f2a04e6bb2623dd5616372dd14e598a',
    'src/backend/backtest_journal_memory.py': 'c118c12f2818fd0389d5503abb51e46fb45a9b05b8f2944dad0fc165d8e871e1',
    'src/backend/backtest_typed_projection.py': 'e4d256026cc694c4321457d27cbf200d208ac5f40b91049cf81964c27cf2f0e0',
    'src/backend/backtest_typed_publisher.py': '10cc6505a7704ce403e8b8fb4ff6db8637dc09d8f6bdd58d93171854d2ed5e48',
    'src/trading_runtime/arte_strategy_one_entry_journal.py': '4c04dbe3b0a57ddebed4b07124bc4f31ae1b729385cd74c16c666ec8390d5b37',
    'src/trading_runtime/arte_journal_writer.py': 'cb91402b02e58f4c6ec2697f7cb764fc285b557128232aa0c6fc3de2eae6bea1',
    'src/trading_runtime/arte_journal_commit_v4.py': 'e64437ceee0702932aa2c39a6975e99deb13994887818366e2709da0722057e8',
    'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
    'src/backend/replay_run_service.py': '0555a1ca78361ee63b6e4caf8a021f998aaa2f2f32ca876a380c37297ae43056',
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
