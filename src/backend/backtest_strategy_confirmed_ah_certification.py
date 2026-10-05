"""Pin the additional AH source graph; inherited runtime proof is separate."""
import ast
from hashlib import sha256
import json
from pathlib import Path


CONFIRMED_AH_SOURCE_AST = {'src/trading_runtime/strategy_confirmed_ah_risk_failure.py': 'caa9e0df4a6e24deaf542d686f912bd598d3a360df5f3131c666753e79bfc598',
 'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '22ed83b6d42e085a8b8b59ac40fe3a3ce06e29e9fded220fa8d75e89a2bff66e',
 'src/trading_runtime/strategy_confirmed_ah_failure_source.py': 'f1fcc57eddb5722116e49acf7d4a2d815d8ceb34fbbe10d44b0699cdc66c3231',
 'src/trading_runtime/arte_confirmed_ah_failure_v4.py': '9dc51903b6016a819f2c457d84872bea440ed6ae20a2b355fb97da83229c0f20',
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
