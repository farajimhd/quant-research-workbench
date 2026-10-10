"""Closed successor source proof; no publication, data or order authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

BASE_CERTIFIER = 'src/backend/backtest_fixed_structural_lot_certification_v31.py'
BASE_AST = '046a7dd3cdfaa6c7bffaa9f93b045df1852ffdf04d8897d1a80d92aebc34b8f4'
ADDITIONAL_SOURCE_AST = {'src/trading_runtime/price_confirmed_original_risk.py': '526c50b7300bf7a1e1c3007bca44e93104089c8e0a87af0228a646632c931aa4', 'src/backend/backtest_price_risk_compatibility.py': '6513ebfdf463f9ebd11f660d08d4f154988624641c1d3d3b83a568140e5a262f', 'src/trading_runtime/strategy_one_hundred_thirteen_release.py': '3d578163cf002a135112f63857278568fc4c1192c485d58c0c3e9b23a5fedcd5', 'src/trading_runtime/strategy_one_hundred_thirteen_contract.py': 'ef5142323961eeeea23d8694b42e0f5bd25f3e9fdc117915f561b6eaeb3067b3', 'src/trading_runtime/strategy_one_hundred_thirteen_configuration.py': '18a438f93df8f6dc1c327e8cde7bcad328c61b60e43b7b0cfbe5680393f9b5a3', 'src/trading_runtime/historical_parent_source_proof.py': '6c136668b0eeeeb111d6358231775b14333dfcfd92e60867e7be34b605c6716d', 'src/backend/backtest_price_risk_native_compatibility.py': '4006285c7c4de84f6e0152c1a33b343e8a20a92291679eb9b5cb7fa451714276', 'src/backend/backtest_price_risk_certification.py': 'd63f22a4c6650807e5f91b62292f8db201e5c448c87f8621b8ee53ed4d6af94a', 'src/backend/backtest_consecutive_price_risk_compatibility.py': '311c763fa391034dbea0eca49f8caf1e49ea3dd713db9f24f88c69e9f9cb71eb', 'src/trading_runtime/consecutive_price_confirmed_risk.py': '4d714b0bc6fa9e996cf6f374a9d6d3eb94138caa14c74cd131d368ff2778e2c6', 'src/trading_runtime/strategy_one_hundred_fourteen_release.py': '4a495b88a2c419ffdcd74cd97ede3a50b94c72b0068caa3e7fde58584bd30fa8', 'src/trading_runtime/strategy_one_hundred_fourteen_contract.py': '02f3deb70a4e9978d9ee42ef2dbff5624d08867e080e56093951c41d78a4cb4d', 'src/trading_runtime/strategy_one_hundred_fourteen_configuration.py': 'ca2b33fe01ca088af5f38fd1d99621b12f8e319cbd26ec5e3e8330f0f5467c19', 'src/backend/backtest_consecutive_price_risk_certification.py': 'cfe8a36c7fa451d9f317eed7958bb6e16bdab6ffc29ab2f5338632f680147206', 'src/backend/backtest_combined_price_risk_compatibility.py': 'da82abb18029d05df0e4f156c4e58340b1315d52a36d2da7fcf20ffa8a37a45b', 'src/trading_runtime/strategy_one_hundred_fifteen_release.py': 'cc6c67e2dad7cf4f462d327f37a3b0cf8f14c9e0fee070cfd9e7d8a09c754d1e', 'src/trading_runtime/strategy_one_hundred_fifteen_contract.py': '9d3efbe3b9c81e5292604300912711d70ef1ff60dadda2eed7827c9fe84545e7', 'src/trading_runtime/strategy_one_hundred_fifteen_configuration.py': '71b2c29500f6015242167e296bc2217bb24bc3ad1c9a8eb04e6bfc241987f244', 'scripts/clickhouse/provision_backtest_v4_original_risk_entry_cost_runner.py': 'ef40cc982876997eb7843b89ab993cacba9396454909e0d36b81340279dead35', 'src/backend/backtest_combined_price_risk_certification.py': '96d8664f73334bc6284a3de84cca3b5d14f6ec6b543852d1f7f44be335babe89', 'src/trading_runtime/bounded_verification_operation.py': '7e25638244ae0c5e2a7d5d93db67619763b6900a666fbd8ed9932e73c9ec2a85', 'src/trading_runtime/original_risk_verification_reuse.py': 'cd8dadbdb83c6007c5bf26177b4fd9b60a6d9ca60b47aecbf32fa356fbe9e0e9', 'src/backend/backtest_original_risk_owned_reader.py': '779bcd0746f1e25c40e676035dc50ad07cee05de93f60d70a4d9670529d26458', 'src/trading_runtime/strategy_one_hundred_sixteen_release.py': 'eaf0979ddf24478276c59a5136e43b6a57217be13586b442b7da83eed6f2f0e0', 'src/trading_runtime/strategy_one_hundred_sixteen_contract.py': 'f4381b9fdf379844340b2ef8e19163e510296efb4d276e330d6483a4ceaf969f', 'src/trading_runtime/strategy_one_hundred_sixteen_configuration.py': 'd9042a3e28555231458abaae3dc9071b8cf19a145bb807dd396270e70927ed0c', 'src/backend/backtest_original_risk_reuse_compatibility.py': '907283e2292d5370a198a3860b2d2c70819f65545f2d1adce66eab995fb397c9'}
APPROVED_SELF_AST = 'e4589dadf40ba822d88528240a25378b0a96b689a25a1faeddf6ea75185e0181'


def _digest(source):
    return sha256(ast.unparse(ast.parse(source)).encode()).hexdigest()


def _read_local(root, relative):
    if (type(relative) is not str or not relative.startswith(('src/', 'scripts/'))
            or '..' in relative.split('/') or '\\' in relative or not relative.endswith('.py')):
        raise ValueError('Original-risk reuse source path is malformed')
    path = root / relative
    if not path.is_file() or path.is_symlink() or root not in path.resolve().parents:
        raise ValueError('Original-risk reuse source path is missing or foreign: ' + relative)
    return path.read_text(encoding='utf-8')


def _literal(tree, name):
    matches = [node for node in tree.body if type(node) is ast.Assign
        and len(node.targets) == 1 and type(node.targets[0]) is ast.Name
        and node.targets[0].id == name]
    if len(matches) != 1:
        raise ValueError('Original-risk reuse source declaration cardinality differs: ' + name)
    return ast.literal_eval(matches[0].value)


def certify_original_risk_reuse_source():
    """Verify complete approved parent inventory and exact selected extensions."""
    root = Path(__file__).resolve().parents[2]
    own_relative = 'src/backend/backtest_original_risk_reuse_certification.py'
    own_source = _read_local(root, own_relative)
    own_tree = ast.parse(own_source)
    names = ('BASE_CERTIFIER', 'BASE_AST', 'ADDITIONAL_SOURCE_AST', 'APPROVED_SELF_AST')
    loaded = dict(zip(names, (BASE_CERTIFIER, BASE_AST, ADDITIONAL_SOURCE_AST, APPROVED_SELF_AST)))
    fresh = {name: _literal(own_tree, name) for name in names}
    if fresh != loaded:
        raise ValueError('Original-risk reuse loaded and fresh declarations differ')
    for node in own_tree.body:
        if (type(node) is ast.Assign and len(node.targets) == 1
                and type(node.targets[0]) is ast.Name and node.targets[0].id in names):
            node.value = ast.Constant(None)
    if sha256(ast.unparse(own_tree).encode()).hexdigest() != APPROVED_SELF_AST:
        raise ValueError('Original-risk reuse certifier envelope differs')
    expected_new = ('src/trading_runtime/price_confirmed_original_risk.py', 'src/backend/backtest_price_risk_compatibility.py', 'src/trading_runtime/strategy_one_hundred_thirteen_release.py', 'src/trading_runtime/strategy_one_hundred_thirteen_contract.py', 'src/trading_runtime/strategy_one_hundred_thirteen_configuration.py', 'src/trading_runtime/historical_parent_source_proof.py', 'src/backend/backtest_price_risk_native_compatibility.py', 'src/backend/backtest_price_risk_certification.py', 'src/backend/backtest_consecutive_price_risk_compatibility.py', 'src/trading_runtime/consecutive_price_confirmed_risk.py', 'src/trading_runtime/strategy_one_hundred_fourteen_release.py', 'src/trading_runtime/strategy_one_hundred_fourteen_contract.py', 'src/trading_runtime/strategy_one_hundred_fourteen_configuration.py', 'src/backend/backtest_consecutive_price_risk_certification.py', 'src/backend/backtest_combined_price_risk_compatibility.py', 'src/trading_runtime/strategy_one_hundred_fifteen_release.py', 'src/trading_runtime/strategy_one_hundred_fifteen_contract.py', 'src/trading_runtime/strategy_one_hundred_fifteen_configuration.py', 'scripts/clickhouse/provision_backtest_v4_original_risk_entry_cost_runner.py', 'src/backend/backtest_combined_price_risk_certification.py', 'src/trading_runtime/bounded_verification_operation.py', 'src/trading_runtime/original_risk_verification_reuse.py', 'src/backend/backtest_original_risk_owned_reader.py', 'src/trading_runtime/strategy_one_hundred_sixteen_release.py', 'src/trading_runtime/strategy_one_hundred_sixteen_contract.py', 'src/trading_runtime/strategy_one_hundred_sixteen_configuration.py', 'src/backend/backtest_original_risk_reuse_compatibility.py')
    if type(ADDITIONAL_SOURCE_AST) is not dict or tuple(ADDITIONAL_SOURCE_AST) != expected_new:
        raise ValueError('Original-risk reuse complete extension inventory differs')
    if any(type(pin) is not str or len(pin) != 64
            or any(c not in '0123456789abcdef' for c in pin)
            for pin in (BASE_AST, APPROVED_SELF_AST, *ADDITIONAL_SOURCE_AST.values())):
        raise ValueError('Original-risk reuse source approval is absent or malformed')
    sealed = [(own_relative, own_source)]
    observed = [('certifier_envelope', APPROVED_SELF_AST)]
    for relative, expected in ADDITIONAL_SOURCE_AST.items():
        source = _read_local(root, relative)
        if _digest(source) != expected:
            raise ValueError('Original-risk reuse extension source changed: ' + relative)
        sealed.append((relative, source))
        observed.append((relative, sha256(source.encode()).hexdigest()))
    base_source = _read_local(root, BASE_CERTIFIER)
    if _digest(base_source) != BASE_AST:
        raise ValueError('Original-risk reuse reviewed parent certifier changed')
    base_tree = ast.parse(base_source)
    required = _literal(base_tree, 'REQUIRED_SOURCE_FILES')
    pins = _literal(base_tree, 'REVIEWED_SOURCE_AST')
    if (type(required) is not tuple or len(required) != 586
            or type(pins) is not dict or tuple(pins) != required or len(set(required)) != len(required)
            or set(required) & set(ADDITIONAL_SOURCE_AST)):
        raise ValueError('Original-risk reuse exact parent inventory differs')
    from .backtest_price_risk_compatibility import PARENT_AST_HASHES, restore_price_risk_parent_source
    from .backtest_price_risk_native_compatibility import restore_native_price_risk_parent_source
    if not set(PARENT_AST_HASHES) <= set(required):
        raise ValueError('Original-risk reuse restoration is outside parent inventory')
    from .backtest_combined_price_risk_compatibility import restore_combined_price_risk_parent_source
    for relative, expected in pins.items():
        # Parent inventory also contains reviewed pipelines/scripts/research code.
        if (type(relative) is not str or not relative.startswith(('src/', 'pipelines/', 'scripts/', 'research/'))
                or '..' in relative.split('/') or '\\' in relative or not relative.endswith('.py')):
            raise ValueError('Original-risk reuse parent source path is malformed')
        path = root / relative
        if not path.is_file() or path.is_symlink() or root not in path.resolve().parents:
            raise ValueError('Original-risk reuse parent source path is missing or foreign: ' + relative)
        source = path.read_text(encoding='utf-8')
        from .backtest_original_risk_reuse_compatibility import restore_original_risk_reuse_parent_source
        restored_current = restore_original_risk_reuse_parent_source(source, relative)
        retained = restore_combined_price_risk_parent_source(restored_current, relative)
        retained = restore_native_price_risk_parent_source(retained, relative)
        retained = restore_price_risk_parent_source(retained, relative) if relative in PARENT_AST_HASHES else retained
        if _digest(retained) != expected:
            raise ValueError('Original-risk reuse retained parent source changed: ' + relative)
        sealed.append((relative, source))
        observed.append((relative, sha256(source.encode()).hexdigest()))
    sealed.append((BASE_CERTIFIER, base_source))
    observed.append((BASE_CERTIFIER, sha256(base_source.encode()).hexdigest()))
    for relative, source in sealed:
        path = root / relative
        if path.is_symlink() or root not in path.resolve().parents or path.read_text(encoding='utf-8') != source:
            raise ValueError('Original-risk reuse source changed during certification: ' + relative)
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
