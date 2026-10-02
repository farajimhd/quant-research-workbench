"""Reviewed Strategy37 episode-prefix and installed release source authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

EPISODE_ACTIVITY_SOURCE_AST = {'src/trading_runtime/strategy_episode_activity_veto.py': 'cb96806e4594bdb2b087b1c12925ee80d296e1313984dce6329f3eee4b911e06', 'src/backend/backtest_strategy_episode_activity_gate.py': '43eaa3e95701f32ab02f5af501ca0e019b03ed91badb8f1bcb62ced3918510b5', 'src/backend/backtest_strategy_episode_activity_source.py': '9d53ade77cdcaac72e097968ad90bbedce4beaaf8866ca4c2f6104d441f7788a', 'src/trading_runtime/strategy_thirty_seven_release.py': '6966c161682e06421a8a0112980a3a487ac4485ca9d2d86700d6621ef3687892', 'pipelines/strategy_one/strategy_thirty_seven_configuration.py': 'a4af45a5b3a4caca5de7eeee6f56cde807013fee3ec1f4e462e0a0f9027c29fa', 'pipelines/strategy_one/configuration_publisher.py': '3021ab1f454ea0d74f36623c2f7aaaeafe917a7abb56116d39aa127f02400f5f', 'scripts/clickhouse/publish_strategy_thirty_seven_configuration.py': 'd7489b4edacd0a96aaf3521f189151398a832e3d60c04c5f402d4652c7ee2825'}


def certify_episode_activity_source(*, source_overrides=None):
    """Pin the additional policy and transport; inherited routes are certified separately."""
    overrides = source_overrides or {}
    if set(overrides) - set(EPISODE_ACTIVITY_SOURCE_AST):
        raise ValueError('Episode source override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in EPISODE_ACTIVITY_SOURCE_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Episode source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy37 pinned episode source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
