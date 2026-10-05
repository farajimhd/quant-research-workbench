"""Pin the additional AH source graph; inherited runtime proof is separate."""
import ast
from hashlib import sha256
import json
from pathlib import Path


CONFIRMED_AH_SOURCE_AST = {'src/trading_runtime/strategy_confirmed_ah_risk_failure.py': 'caa9e0df4a6e24deaf542d686f912bd598d3a360df5f3131c666753e79bfc598',
 'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '5f9b567fe7f3979aab453850eb7d0f884c2f58a6d9e4111563e52c1d54859b13',
 'src/trading_runtime/strategy_confirmed_ah_failure_source.py': 'ee8d344e486a6252da939dedb656a852a7c24a3324054f02506d49f78df64e10',
 'src/trading_runtime/arte_confirmed_ah_failure_v4.py': 'c17862a35ad567c110523d0e770a668fc0751b36ad3aedc51ccbe239a508294c',
 'src/trading_runtime/strategy_thirty_four_release.py': '16a38bfab06bf59baa162907010724b731e76301ab4dc4c86b1e6f61a907a24b',
 'pipelines/strategy_one/strategy_thirty_four_configuration.py': '8fa2652ce8e4975a4ce97bc7ce6c39d9ba68fb8563ff8eb8471c6971af80e335'}


def certify_confirmed_ah_source(*, source_overrides=None):
    """Reject any changed additional rule, factory, source or publication policy.

    This supplements the inherited complete runtime/publisher/OMS inventory.
    It does not certify native tables, source data, a connected run or profit.
    """
    overrides = source_overrides or {}
    if set(overrides) - set(CONFIRMED_AH_SOURCE_AST):
        raise ValueError('AH source override is outside its pinned authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in CONFIRMED_AH_SOURCE_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('AH source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy 34 pinned AH source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
