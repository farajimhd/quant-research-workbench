"""Closed successor source proof; no publication, data or order authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

BASE_CERTIFIER = 'src/backend/backtest_fixed_structural_lot_certification_v31.py'
BASE_AST = '046a7dd3cdfaa6c7bffaa9f93b045df1852ffdf04d8897d1a80d92aebc34b8f4'
ADDITIONAL_SOURCE_AST = {'src/trading_runtime/price_confirmed_original_risk.py': '526c50b7300bf7a1e1c3007bca44e93104089c8e0a87af0228a646632c931aa4', 'src/backend/backtest_price_risk_compatibility.py': '6513ebfdf463f9ebd11f660d08d4f154988624641c1d3d3b83a568140e5a262f', 'src/trading_runtime/strategy_one_hundred_thirteen_release.py': '3d578163cf002a135112f63857278568fc4c1192c485d58c0c3e9b23a5fedcd5', 'src/trading_runtime/strategy_one_hundred_thirteen_contract.py': 'ef5142323961eeeea23d8694b42e0f5bd25f3e9fdc117915f561b6eaeb3067b3', 'src/trading_runtime/strategy_one_hundred_thirteen_configuration.py': '18a438f93df8f6dc1c327e8cde7bcad328c61b60e43b7b0cfbe5680393f9b5a3', 'src/trading_runtime/historical_parent_source_proof.py': '6c136668b0eeeeb111d6358231775b14333dfcfd92e60867e7be34b605c6716d', 'src/backend/backtest_price_risk_native_compatibility.py': '4006285c7c4de84f6e0152c1a33b343e8a20a92291679eb9b5cb7fa451714276'}
APPROVED_SELF_AST = 'a1212d495fcc7bcbc06e86d1f8e3f93539250efcee587c9113b103c544fd26c5'


def _digest(source):
    return sha256(ast.unparse(ast.parse(source)).encode()).hexdigest()


def _read_local(root, relative):
    if (type(relative) is not str or not relative.startswith('src/')
            or '..' in relative.split('/') or '\\' in relative or not relative.endswith('.py')):
        raise ValueError('Price-risk source path is malformed')
    path = root / relative
    if not path.is_file() or path.is_symlink() or root not in path.resolve().parents:
        raise ValueError('Price-risk source path is missing or foreign: ' + relative)
    return path.read_text(encoding='utf-8')


def _literal(tree, name):
    matches = [node for node in tree.body if type(node) is ast.Assign
        and len(node.targets) == 1 and type(node.targets[0]) is ast.Name
        and node.targets[0].id == name]
    if len(matches) != 1:
        raise ValueError('Price-risk source declaration cardinality differs: ' + name)
    return ast.literal_eval(matches[0].value)


def certify_price_confirmed_original_risk_source():
    """Verify complete approved parent inventory and exact selected extensions."""
    root = Path(__file__).resolve().parents[2]
    own_relative = 'src/backend/backtest_price_risk_certification.py'
    own_source = _read_local(root, own_relative)
    own_tree = ast.parse(own_source)
    names = ('BASE_CERTIFIER', 'BASE_AST', 'ADDITIONAL_SOURCE_AST', 'APPROVED_SELF_AST')
    loaded = dict(zip(names, (BASE_CERTIFIER, BASE_AST, ADDITIONAL_SOURCE_AST, APPROVED_SELF_AST)))
    fresh = {name: _literal(own_tree, name) for name in names}
    if fresh != loaded:
        raise ValueError('Price-risk loaded and fresh declarations differ')
    for node in own_tree.body:
        if (type(node) is ast.Assign and len(node.targets) == 1
                and type(node.targets[0]) is ast.Name and node.targets[0].id in names):
            node.value = ast.Constant(None)
    if sha256(ast.unparse(own_tree).encode()).hexdigest() != APPROVED_SELF_AST:
        raise ValueError('Price-risk certifier envelope differs')
    expected_new = (
        'src/trading_runtime/price_confirmed_original_risk.py',
        'src/backend/backtest_price_risk_compatibility.py',
        'src/trading_runtime/strategy_one_hundred_thirteen_release.py',
        'src/trading_runtime/strategy_one_hundred_thirteen_contract.py',
        'src/trading_runtime/strategy_one_hundred_thirteen_configuration.py',
        'src/trading_runtime/historical_parent_source_proof.py',
        'src/backend/backtest_price_risk_native_compatibility.py',
    )
    if type(ADDITIONAL_SOURCE_AST) is not dict or tuple(ADDITIONAL_SOURCE_AST) != expected_new:
        raise ValueError('Price-risk complete extension inventory differs')
    if any(type(pin) is not str or len(pin) != 64
            or any(c not in '0123456789abcdef' for c in pin)
            for pin in (BASE_AST, APPROVED_SELF_AST, *ADDITIONAL_SOURCE_AST.values())):
        raise ValueError('Price-risk source approval is absent or malformed')
    sealed = [(own_relative, own_source)]
    observed = [('certifier_envelope', APPROVED_SELF_AST)]
    for relative, expected in ADDITIONAL_SOURCE_AST.items():
        source = _read_local(root, relative)
        if _digest(source) != expected:
            raise ValueError('Price-risk extension source changed: ' + relative)
        sealed.append((relative, source))
        observed.append((relative, sha256(source.encode()).hexdigest()))
    base_source = _read_local(root, BASE_CERTIFIER)
    if _digest(base_source) != BASE_AST:
        raise ValueError('Price-risk reviewed parent certifier changed')
    base_tree = ast.parse(base_source)
    required = _literal(base_tree, 'REQUIRED_SOURCE_FILES')
    pins = _literal(base_tree, 'REVIEWED_SOURCE_AST')
    if (type(required) is not tuple or len(required) != 586
            or type(pins) is not dict or tuple(pins) != required or len(set(required)) != len(required)
            or set(required) & set(ADDITIONAL_SOURCE_AST)):
        raise ValueError('Price-risk exact parent inventory differs')
    from .backtest_price_risk_compatibility import PARENT_AST_HASHES, restore_price_risk_parent_source
    from .backtest_price_risk_native_compatibility import restore_native_price_risk_parent_source
    if not set(PARENT_AST_HASHES) <= set(required):
        raise ValueError('Price-risk restoration is outside parent inventory')
    for relative, expected in pins.items():
        # Parent inventory also contains reviewed pipelines/scripts/research code.
        if (type(relative) is not str or not relative.startswith(('src/', 'pipelines/', 'scripts/', 'research/'))
                or '..' in relative.split('/') or '\\' in relative or not relative.endswith('.py')):
            raise ValueError('Price-risk parent source path is malformed')
        path = root / relative
        if not path.is_file() or path.is_symlink() or root not in path.resolve().parents:
            raise ValueError('Price-risk parent source path is missing or foreign: ' + relative)
        source = path.read_text(encoding='utf-8')
        retained = restore_native_price_risk_parent_source(source, relative)
        retained = restore_price_risk_parent_source(retained, relative) if relative in PARENT_AST_HASHES else retained
        if _digest(retained) != expected:
            raise ValueError('Price-risk retained parent source changed: ' + relative)
        sealed.append((relative, source))
        observed.append((relative, sha256(source.encode()).hexdigest()))
    sealed.append((BASE_CERTIFIER, base_source))
    observed.append((BASE_CERTIFIER, sha256(base_source.encode()).hexdigest()))
    for relative, source in sealed:
        path = root / relative
        if path.is_symlink() or root not in path.resolve().parents or path.read_text(encoding='utf-8') != source:
            raise ValueError('Price-risk source changed during certification: ' + relative)
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
