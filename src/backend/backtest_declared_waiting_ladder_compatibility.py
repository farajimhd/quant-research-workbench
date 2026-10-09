"""Exact reviewed waiting-ladder edits reversed only for retained parent proof."""
import ast
from hashlib import sha256
import json
from pathlib import Path
REVIEWED_EDITS = {'trading_runtime/strategy_registry.py': {'current_ast': 'ef1c36159a223ce6aacdd17a7f4feed61c061c4f36a3e6bd6951f2b7db4e8e25',
                                          'parent_ast': 'ed9a3abf621c38caefb3442a5a8a897db2a644a83907e255fa8d99113e6238d2',
                                          'edits': [('    strategy_factory: StrategyFactory\n'
                                                     '    mode: str = "backtest"\n'
                                                     '    manifest_authority: Any | None = None\n'
                                                     '\n'
                                                     '    @property\n',
                                                     '    strategy_factory: StrategyFactory\n'
                                                     '    mode: str = "backtest"\n'
                                                     '\n'
                                                     '    @property\n'),
                                                    ('                or not '
                                                     'callable(self.strategy_factory)):\n'
                                                     '            raise ValueError("Fixed executor needs an '
                                                     'installed Backtest-only contract")\n'
                                                     '        if self.manifest_authority is not None:\n'
                                                     '            from .declared_native_manifest import '
                                                     'NativeManifestAuthority\n'
                                                     '            if type(self.manifest_authority) is not '
                                                     'NativeManifestAuthority:\n'
                                                     '                raise ValueError("Fixed executor '
                                                     'manifest authority type differs")\n'
                                                     '            self.manifest_authority.verify()\n'
                                                     '        contract = self.contract_factory()\n'
                                                     '        if (contract.strategy_id, '
                                                     'contract.strategy_number, contract.execution_interval) '
                                                     '!= (\n',
                                                     '                or not '
                                                     'callable(self.strategy_factory)):\n'
                                                     '            raise ValueError("Fixed executor needs an '
                                                     'installed Backtest-only contract")\n'
                                                     '        contract = self.contract_factory()\n'
                                                     '        if (contract.strategy_id, '
                                                     'contract.strategy_number, contract.execution_interval) '
                                                     '!= (\n'),
                                                    ('def numbered_strategy_parent(number: int) -> int:\n'
                                                     '    """Explicit immutable inheritance; Strategy 8 '
                                                     'branches from 6, not 7."""\n'
                                                     '    from .declared_native_manifest import '
                                                     'registered_manifest_authority\n'
                                                     '    authority = registered_manifest_authority(number)\n'
                                                     '    if authority is not None:\n'
                                                     '        return authority.parent_number\n'
                                                     '    parents = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: '
                                                     '6, 9: 8, 10: 9, 11: 10, 12: 11, 13: 12, 14: 13, 15: '
                                                     '14, 16: 15, 17: 14, 18: 17, 19: 18, 20: 19, 21: 20, '
                                                     '22: 21, 23: 22, 24: 23, 25: 24, 26: 25, 27: 26, 28: '
                                                     '27, 29: 28, 30: 29, 31: 30, 32: 31, 33: 32, 34: 33, '
                                                     '35: 34, 36: 35, 37: 36, 38: 37, 39: 38, 40: 39, 41: '
                                                     '40, 42: 41, 46: 42, 47: 46, 48: 42, 49: 42, 50: 42, '
                                                     '51: 42, 52: 50, 53: 50, 54: 50, 55: 50, 56: 50, 57: '
                                                     '50, 58: 50, 59: 50, 60: 50, 61: 50, 64: 42, 65: 42, '
                                                     '66: 42, 68: 42, 69: 68, 70: 69, 71: 70, 72: 70, 73: '
                                                     '72, 74: 73, 77: 42, 80: 42, 81: 42, 82: 42, 83: 42, '
                                                     '84: 42, 85: 42, 86: 42, 87: 42, 88: 42, 89: 42, 90: '
                                                     '42, 92: 42, 93: 42, 94: 42, 95: 42, 98: 42, 99: 42}\n'
                                                     '    if type(number) is not int or number not in '
                                                     'parents:\n',
                                                     'def numbered_strategy_parent(number: int) -> int:\n'
                                                     '    """Explicit immutable inheritance; Strategy 8 '
                                                     'branches from 6, not 7."""\n'
                                                     '    parents = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: '
                                                     '6, 9: 8, 10: 9, 11: 10, 12: 11, 13: 12, 14: 13, 15: '
                                                     '14, 16: 15, 17: 14, 18: 17, 19: 18, 20: 19, 21: 20, '
                                                     '22: 21, 23: 22, 24: 23, 25: 24, 26: 25, 27: 26, 28: '
                                                     '27, 29: 28, 30: 29, 31: 30, 32: 31, 33: 32, 34: 33, '
                                                     '35: 34, 36: 35, 37: 36, 38: 37, 39: 38, 40: 39, 41: '
                                                     '40, 42: 41, 46: 42, 47: 46, 48: 42, 49: 42, 50: 42, '
                                                     '51: 42, 52: 50, 53: 50, 54: 50, 55: 50, 56: 50, 57: '
                                                     '50, 58: 50, 59: 50, 60: 50, 61: 50, 64: 42, 65: 42, '
                                                     '66: 42, 68: 42, 69: 68, 70: 69, 71: 70, 72: 70, 73: '
                                                     '72, 74: 73, 77: 42, 80: 42, 81: 42, 82: 42, 83: 42, '
                                                     '84: 42, 85: 42, 86: 42, 87: 42, 88: 42, 89: 42, 90: '
                                                     '42, 92: 42, 93: 42, 94: 42, 95: 42, 98: 42, 99: 42}\n'
                                                     '    if type(number) is not int or number not in '
                                                     'parents:\n'),
                                                    ('\n'
                                                     'def numbered_strategy(number: int) -> '
                                                     'NumberedStrategyRelease:\n'
                                                     '    if number in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, '
                                                     '13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, '
                                                     '26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, '
                                                     '39, 40, 41, 42, 46, 47, 48, 49, 50, 51, 52, 53, 54, '
                                                     '55, 56, 57, 58, 59, 60, 61, 64, 65, 66, 68, 69, 70, '
                                                     '71, 72, 73, 74, 77, 80, 81, 82, 83, 84, 85, 86, 87, '
                                                     '88, 89, 90, 92, 93, 94, 95, 97, 98, 99):\n'
                                                     '        initialize_numbered_fixed_strategies()\n'
                                                     '    with _LOCK:\n',
                                                     '\n'
                                                     'def numbered_strategy(number: int) -> '
                                                     'NumberedStrategyRelease:\n'
                                                     '    if number in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, '
                                                     '13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, '
                                                     '26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, '
                                                     '39, 40, 41, 42, 46, 47, 48, 49, 50, 51, 52, 53, 54, '
                                                     '55, 56, 57, 58, 59, 60, 61, 64, 65, 66, 68, 69, 70, '
                                                     '71, 72, 73, 74, 77, 80, 81, 82, 83, 84, 85, 86, 87, '
                                                     '88, 89, 90, 92, 93, 94, 95, 98, 99):\n'
                                                     '        initialize_numbered_fixed_strategies()\n'
                                                     '    with _LOCK:\n'),
                                                    ('            '
                                                     'contract_factory=strategy_sixty_five_contract, '
                                                     'strategy_factory=AssignedWaitingSwingLadder65))\n'
                                                     '        register_numbered_strategy(sixty_fifth)\n'
                                                     '        from .strategy_ninety_seven_release import '
                                                     '(release_contract as waiting_observation_release,\n'
                                                     '            '
                                                     'derive_strategy_ninety_seven_configuration, '
                                                     'verify_strategy_ninety_seven_manifest)\n'
                                                     '        from .strategy_ninety_seven_contract import '
                                                     'strategy_ninety_seven_contract, '
                                                     'AssignedWaitingSwingLadder97\n'
                                                     '        from .declared_native_manifest import '
                                                     'NativeManifestAuthority\n'
                                                     '        from .strategy_forty_two_release import '
                                                     'release_contract as waiting_parent_release\n'
                                                     '        from '
                                                     'src.backend.backtest_declared_waiting_ladder_certification '
                                                     'import certify_declared_waiting_ladder_source\n'
                                                     '        waiting_observation = '
                                                     'waiting_observation_release()\n'
                                                     '        '
                                                     'register_fixed_strategy_executor(FixedStrategyExecutorRegistration(\n'
                                                     '            '
                                                     'strategy_id=waiting_observation.executor_strategy_id,\n'
                                                     '            '
                                                     'revision=waiting_observation.executor_revision,\n'
                                                     '            '
                                                     'evaluation_interval=waiting_observation.evaluation_interval,\n'
                                                     '            '
                                                     'contract_factory=strategy_ninety_seven_contract, '
                                                     'strategy_factory=AssignedWaitingSwingLadder97,\n'
                                                     '            '
                                                     'manifest_authority=NativeManifestAuthority(42, '
                                                     "'strategy-ninety-seven-from',\n"
                                                     '                '
                                                     'derive_strategy_ninety_seven_configuration, '
                                                     'verify_strategy_ninety_seven_manifest,\n'
                                                     '                '
                                                     'certify_declared_waiting_ladder_source, '
                                                     'waiting_parent_release)))\n'
                                                     '        '
                                                     'register_numbered_strategy(waiting_observation)\n'
                                                     '        from .strategy_fifty_release import '
                                                     'release_contract as fiftieth_release_contract\n'
                                                     '        from .strategy_fifty_contract import '
                                                     'strategy_fifty_contract\n',
                                                     '            '
                                                     'contract_factory=strategy_sixty_five_contract, '
                                                     'strategy_factory=AssignedWaitingSwingLadder65))\n'
                                                     '        register_numbered_strategy(sixty_fifth)\n'
                                                     '        from .strategy_fifty_release import '
                                                     'release_contract as fiftieth_release_contract\n'
                                                     '        from .strategy_fifty_contract import '
                                                     'strategy_fifty_contract\n')]},
 'trading_runtime/numbered_fixed_strategy.py': {'current_ast': 'a94f7b006a96f706b1f6770e4ea396cd2fd0bc21f11d69c1ba9ab816f09e11c6',
                                                'parent_ast': '4037436deb90717ec193246eac5269374e5ffaf2f6d238e1b46cbced4bb12714',
                                                'edits': [('\n'
                                                           '\n'
                                                           'def automatic_ladder_qualification(release):\n'
                                                           '    rules = '
                                                           "{'completed-vwap-below-above-cross@1': "
                                                           "'vwap_cross',\n"
                                                           '             '
                                                           "'completed-first-eligible-above-vwap@1': "
                                                           "'first_eligible_above_vwap'}\n"
                                                           '    selected = tuple(rule for rule in '
                                                           'release.rule_set_contracts if rule in rules)\n'
                                                           '    if len(selected) != 1:\n'
                                                           "        raise ValueError('Automatic ladder needs "
                                                           "exactly one supported qualification rule')\n"
                                                           '    return selected[0], rules[selected[0]]\n'
                                                           '\n'
                                                           '\n'
                                                           'def declared_automatic_ladder_release(number):\n'
                                                           '    """Recognize only the sealed waiting '
                                                           'adapter\'s exact semantic contract."""\n',
                                                           '\n'
                                                           '\n'
                                                           'def declared_automatic_ladder_release(number):\n'
                                                           '    """Recognize only the sealed waiting '
                                                           'adapter\'s exact semantic contract."""\n'),
                                                          ('    required_inputs = (marker, '
                                                           "'ladder-wait-first-complete-geometry-v1',\n"
                                                           '        '
                                                           "'arte.trading_squeeze_ladder_geometry_binding_v1@exact-parent:earliest-causal-pair')\n"
                                                           '    qualification_rule, _ = '
                                                           'automatic_ladder_qualification(release)\n'
                                                           '    required_rules = (\n'
                                                           '        '
                                                           "'ladder-wait-first-complete-geometry-v1', "
                                                           "'certified-early-squeeze-admission@1',\n"
                                                           '        qualification_rule, '
                                                           "'prepared-ladder-strict-liquidity@1',\n"
                                                           "        'later-frozen-v7-upper-break@1', "
                                                           "'confirmed-swing-low-fixed-stop@1',\n"
                                                           '        '
                                                           "'three-nearest-complete-overhead-targets-equal@1',\n",
                                                           '    required_inputs = (marker, '
                                                           "'ladder-wait-first-complete-geometry-v1',\n"
                                                           '        '
                                                           "'arte.trading_squeeze_ladder_geometry_binding_v1@exact-parent:earliest-causal-pair')\n"
                                                           '    required_rules = (\n'
                                                           '        '
                                                           "'ladder-wait-first-complete-geometry-v1', "
                                                           "'certified-early-squeeze-admission@1',\n"
                                                           "        'completed-vwap-below-above-cross@1', "
                                                           "'prepared-ladder-strict-liquidity@1',\n"
                                                           "        'later-frozen-v7-upper-break@1', "
                                                           "'confirmed-swing-low-fixed-stop@1',\n"
                                                           '        '
                                                           "'three-nearest-complete-overhead-targets-equal@1',\n"),
                                                          ('            or contract.automatic_entry_policy '
                                                           '!= AutomaticLadderPolicy()\n'
                                                           '            or '
                                                           'declared_geometry_binding_policy(contract.automatic_market_policy) '
                                                           '!= LadderGeometryBindingPolicy()\n'
                                                           '            or '
                                                           "contract.automatic_market_policy['gate']['qualification_mode'] "
                                                           '!= automatic_ladder_qualification(release)[1]):\n'
                                                           "        raise ValueError('Automatic ladder typed "
                                                           "factory differs from declared waiting policy')\n"
                                                           '    return contract\n',
                                                           '            or contract.automatic_entry_policy '
                                                           '!= AutomaticLadderPolicy()\n'
                                                           '            or '
                                                           'declared_geometry_binding_policy(contract.automatic_market_policy) '
                                                           '!= LadderGeometryBindingPolicy()\n'
                                                           '            or '
                                                           "contract.automatic_market_policy['gate']['qualification_mode'] "
                                                           "!= 'vwap_cross'):\n"
                                                           "        raise ValueError('Automatic ladder typed "
                                                           "factory differs from declared waiting policy')\n"
                                                           '    return contract\n')]},
 'backend/backtest_strategy_one_configuration.py': {'current_ast': '4e65c16f50ebd070f41c88669ef6fff0be054161c5c21dfb20c6f08379e3c9de',
                                                    'parent_ast': '291fe7f7aed8c36743afc08d3420ce224bb191f7c87e5b3968431404d2fb4765',
                                                    'edits': [('        raise RuntimeError(f"Strategy '
                                                               '{strategy_number} needs exactly one '
                                                               'immutable typed configuration release")\n'
                                                               '    release = releases[0]\n'
                                                               '    from '
                                                               'src.trading_runtime.declared_native_manifest '
                                                               'import registered_manifest_authority\n'
                                                               '    authority = '
                                                               'registered_manifest_authority(strategy_number)\n'
                                                               '    if authority is not None and not '
                                                               "str(release.get('source_candidate_id') or "
                                                               "'').startswith(authority.source_prefix + "
                                                               "':'):\n"
                                                               "        raise RuntimeError('Native "
                                                               'configuration source prefix differs from '
                                                               "registered authority')\n"
                                                               '    attempt = '
                                                               'str(release.get("release_attempt_id") or '
                                                               '"")\n'
                                                               '    if (release.get("strategy_id") != '
                                                               'STRATEGY_ID\n',
                                                               '        raise RuntimeError(f"Strategy '
                                                               '{strategy_number} needs exactly one '
                                                               'immutable typed configuration release")\n'
                                                               '    release = releases[0]\n'
                                                               '    attempt = '
                                                               'str(release.get("release_attempt_id") or '
                                                               '"")\n'
                                                               '    if (release.get("strategy_id") != '
                                                               'STRATEGY_ID\n'),
                                                              ('        source_number = '
                                                               'numbered_strategy_parent(strategy_number)\n'
                                                               '        source = '
                                                               'certify_numbered_configuration(client, '
                                                               'source_number)\n'
                                                               '        from '
                                                               'src.trading_runtime.declared_native_manifest '
                                                               'import registered_manifest_authority\n'
                                                               '        authority = '
                                                               'registered_manifest_authority(strategy_number)\n'
                                                               '        if authority is not None:\n'
                                                               '            derive = authority.derive\n'
                                                               '        elif '
                                                               'declared_fixed_structural_lot_contract(strategy_number) '
                                                               'is not None:\n'
                                                               '            from functools import partial\n'
                                                               '            derive = '
                                                               'partial(derive_registered_fixed_structural_lot_configuration, '
                                                               'number=strategy_number)\n',
                                                               '        source_number = '
                                                               'numbered_strategy_parent(strategy_number)\n'
                                                               '        source = '
                                                               'certify_numbered_configuration(client, '
                                                               'source_number)\n'
                                                               '        if '
                                                               'declared_fixed_structural_lot_contract(strategy_number) '
                                                               'is not None:\n'
                                                               '            from functools import partial\n'
                                                               '            derive = '
                                                               'partial(derive_registered_fixed_structural_lot_configuration, '
                                                               'number=strategy_number)\n'),
                                                              ('    if '
                                                               'declared_fixed_structural_lot_contract(number) '
                                                               'is not None:\n'
                                                               '        '
                                                               'verify_fixed_structural_lot_configuration(strategy)\n'
                                                               '        return True\n'
                                                               '    from '
                                                               'src.trading_runtime.declared_native_manifest '
                                                               'import registered_manifest_authority\n'
                                                               '    authority = '
                                                               'registered_manifest_authority(number)\n'
                                                               '    if authority is not None:\n'
                                                               '        authority.verify_manifest(strategy)\n'
                                                               '        return True\n'
                                                               '    if number == 1:\n',
                                                               '    if '
                                                               'declared_fixed_structural_lot_contract(number) '
                                                               'is not None:\n'
                                                               '        '
                                                               'verify_fixed_structural_lot_configuration(strategy)\n'
                                                               '        return True\n'
                                                               '    if number == 1:\n')]},
 'pipelines/strategy_one/configuration_publisher.py': {'current_ast': 'c0fb30741372451c93847f02a029efca753ea3850c232cead77af67dd21b33c8',
                                                       'parent_ast': '433cfe48ec94f539760284fb425381a52f79a655f93813216bc8b65e0f4ebe31',
                                                       'edits': [('\n'
                                                                  '\n'
                                                                  'from '
                                                                  'src.trading_runtime.declared_native_manifest '
                                                                  'import registered_manifest_authority\n'
                                                                  '\n'
                                                                  '\n'
                                                                  'def publish_configuration(client: Any, '
                                                                  'keeper: Any,\n'
                                                                  '                          envelope: '
                                                                  'Mapping[str, Any]) -> str:\n',
                                                                  '\n'
                                                                  '\n'
                                                                  'def publish_configuration(client: Any, '
                                                                  'keeper: Any,\n'
                                                                  '                          envelope: '
                                                                  'Mapping[str, Any]) -> str:\n'),
                                                                 ('    if number == 1:\n'
                                                                  '        payload, nodes = '
                                                                  '_verified_envelope(envelope)\n'
                                                                  '    elif type(number) is int and '
                                                                  '(registered_manifest_authority(number) is '
                                                                  'not None or '
                                                                  'declared_fixed_structural_lot_contract(number) '
                                                                  'is not None or number in (2, 3, 4, 5, 6, '
                                                                  '7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, '
                                                                  '18, 19, 20, 21, 22, 23, 24, 25, 26, 27, '
                                                                  '28, 29, 30, 31, 32, 33, 34, 35, 36, 37, '
                                                                  '38, 39, 40, 41, 42, 46, 47, 48, 49, 50, '
                                                                  '51, 52, 53, 54, 55, 56, 57, 58, 59, 60, '
                                                                  '61, 64, 65, 66, 68, 69, 70, 71, 72, 73, '
                                                                  '74)):\n'
                                                                  '        payload, nodes = '
                                                                  '_verified_numbered_envelope(envelope)\n'
                                                                  '        authority = '
                                                                  'registered_manifest_authority(number)\n'
                                                                  '        if authority is not None:\n'
                                                                  '            compile_configuration = '
                                                                  'authority.derive\n'
                                                                  '        elif '
                                                                  'declared_fixed_structural_lot_contract(number) '
                                                                  'is not None:\n'
                                                                  '            from functools import '
                                                                  'partial\n'
                                                                  '            compile_configuration = '
                                                                  'partial(compile_registered_fixed_structural_lot_configuration, '
                                                                  'number=number)\n',
                                                                  '    if number == 1:\n'
                                                                  '        payload, nodes = '
                                                                  '_verified_envelope(envelope)\n'
                                                                  '    elif type(number) is int and '
                                                                  '(declared_fixed_structural_lot_contract(number) '
                                                                  'is not None or number in (2, 3, 4, 5, 6, '
                                                                  '7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, '
                                                                  '18, 19, 20, 21, 22, 23, 24, 25, 26, 27, '
                                                                  '28, 29, 30, 31, 32, 33, 34, 35, 36, 37, '
                                                                  '38, 39, 40, 41, 42, 46, 47, 48, 49, 50, '
                                                                  '51, 52, 53, 54, 55, 56, 57, 58, 59, 60, '
                                                                  '61, 64, 65, 66, 68, 69, 70, 71, 72, 73, '
                                                                  '74)):\n'
                                                                  '        payload, nodes = '
                                                                  '_verified_numbered_envelope(envelope)\n'
                                                                  '        if '
                                                                  'declared_fixed_structural_lot_contract(number) '
                                                                  'is not None:\n'
                                                                  '            from functools import '
                                                                  'partial\n'
                                                                  '            compile_configuration = '
                                                                  'partial(compile_registered_fixed_structural_lot_configuration, '
                                                                  'number=number)\n'),
                                                                 ('        raise ValueError("Numbered '
                                                                  'configuration envelope shape differs")\n'
                                                                  '    payload = envelope["payload"]\n'
                                                                  '    if not '
                                                                  'is_numbered_fixed_configuration(payload) '
                                                                  'or '
                                                                  '(registered_manifest_authority(payload["strategy"]["strategy_number"]) '
                                                                  'is None and '
                                                                  'declared_fixed_structural_lot_contract(payload["strategy"]["strategy_number"]) '
                                                                  'is None and '
                                                                  'payload["strategy"]["strategy_number"] '
                                                                  'not in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, '
                                                                  '12, 13, 14, 15, 16, 17, 18, 19, 20, 21, '
                                                                  '22, 23, 24, 25, 26, 27, 28, 29, 30, 31, '
                                                                  '32, 33, 34, 35, 36, 37, 38, 39, 40, 41, '
                                                                  '42, 46, 47, 48, 49, 50, 51, 52, 53, 54, '
                                                                  '55, 56, 57, 58, 59, 60, 61, 64, 65, 66, '
                                                                  '68, 69, 70, 71, 72, 73, 74)):\n'
                                                                  '        raise ValueError("Numbered '
                                                                  'publisher requires sealed Strategy 2, 3, '
                                                                  '4, 5, 6, 7, 8, 9, 10, 11 or 12")\n'
                                                                  '    '
                                                                  '_validate_strategy_two_payload(payload)\n'
                                                                  '    manifest = '
                                                                  'payload["strategy"]["numbered_release"]\n'
                                                                  '    authority = '
                                                                  "registered_manifest_authority(payload['strategy']['strategy_number'])\n"
                                                                  '    source_prefix = '
                                                                  'authority.source_prefix if authority is '
                                                                  'not None else '
                                                                  "'fixed-structural-lots-from' if "
                                                                  "declared_fixed_structural_lot_contract(payload['strategy']['strategy_number']) "
                                                                  'is not None else {2: "strategy-two-from", '
                                                                  '3: "strategy-three-from", 4: '
                                                                  '"strategy-four-from", 5: '
                                                                  '"strategy-five-from", 6: '
                                                                  '"strategy-six-from", 7: '
                                                                  '"strategy-seven-from", 8: '
                                                                  '"strategy-eight-from", 9: '
                                                                  '"strategy-nine-from", 10: '
                                                                  '"strategy-ten-from", 11: '
                                                                  '"strategy-eleven-from", 12: '
                                                                  '"strategy-twelve-from", 13: '
                                                                  '"strategy-thirteen-from", 14: '
                                                                  '"strategy-fourteen-from", 15: '
                                                                  '"strategy-fifteen-from", 16: '
                                                                  '"strategy-sixteen-from", 17: '
                                                                  '"strategy-seventeen-from", 18: '
                                                                  '"strategy-eighteen-from", 19: '
                                                                  '"strategy-nineteen-from", 20: '
                                                                  '"strategy-twenty-from", 21: '
                                                                  '"strategy-twenty-one-from", 22: '
                                                                  '"strategy-twenty-two-from", 23: '
                                                                  '"strategy-twenty-three-from", 24: '
                                                                  '"strategy-twenty-four-from", 25: '
                                                                  '"strategy-twenty-five-from", 26: '
                                                                  '"strategy-twenty-six-from", 27: '
                                                                  '"strategy-twenty-seven-from", 28: '
                                                                  '"strategy-twenty-eight-from", 29: '
                                                                  '"strategy-twenty-nine-from", 30: '
                                                                  '"strategy-thirty-from", 31: '
                                                                  '"strategy-thirty-one-from", 32: '
                                                                  '"strategy-thirty-two-from", 33: '
                                                                  '"strategy-thirty-three-from", 34: '
                                                                  '"strategy-thirty-four-from", 35: '
                                                                  '"strategy-thirty-five-from", 36: '
                                                                  '"strategy-thirty-six-from", 37: '
                                                                  '"strategy-thirty-seven-from", 38: '
                                                                  '"strategy-thirty-eight-from", 39: '
                                                                  '"strategy-thirty-nine-from", 40: '
                                                                  '"strategy-forty-from", 41: '
                                                                  '"strategy-forty-one-from", 42: '
                                                                  '"strategy-forty-two-from", 46: '
                                                                  '"strategy-forty-six-from", 47: '
                                                                  '"strategy-forty-seven-from", 48: '
                                                                  '"strategy-forty-eight-from", 49: '
                                                                  '"strategy-forty-nine-from", 50: '
                                                                  '"strategy-fifty-from", 51: '
                                                                  '"strategy-fifty-one-from", 52: '
                                                                  "'strategy-fifty-two-from', 53: "
                                                                  '"strategy-fifty-three-from", 54: '
                                                                  '"strategy-fifty-four-from", 55: '
                                                                  "'strategy-fifty-five-from', 56: "
                                                                  "'strategy-fifty-six-from', 57: "
                                                                  "'strategy-fifty-seven-from', 58: "
                                                                  "'strategy-fifty-eight-from', 59: "
                                                                  "'strategy-fifty-nine-from', 60: "
                                                                  "'strategy-sixty-from', 61: "
                                                                  "'strategy-sixty-one-from', 64: "
                                                                  "'strategy-sixty-four-from', 65: "
                                                                  "'strategy-sixty-five-from', 66: "
                                                                  "'strategy-sixty-six-from', 68: "
                                                                  "'strategy-sixty-eight-from', 69: "
                                                                  "'strategy-sixty-nine-from', 70: "
                                                                  "'strategy-seventy-from', 71: "
                                                                  "'strategy-seventy-one-from', 72: "
                                                                  "'strategy-seventy-two-from', 73: "
                                                                  "'strategy-seventy-three-from', 74: "
                                                                  '\'strategy-seventy-four-from\'}[payload["strategy"]["strategy_number"]]\n'
                                                                  '    if (envelope["source_candidate_id"] '
                                                                  '!= '
                                                                  'f"{source_prefix}:{manifest[\'source_revision_id\']}"\n'
                                                                  '            or '
                                                                  'envelope["source_candidate_hash"] != '
                                                                  'manifest["source_payload_hash"]):\n',
                                                                  '        raise ValueError("Numbered '
                                                                  'configuration envelope shape differs")\n'
                                                                  '    payload = envelope["payload"]\n'
                                                                  '    if not '
                                                                  'is_numbered_fixed_configuration(payload) '
                                                                  'or '
                                                                  '(declared_fixed_structural_lot_contract(payload["strategy"]["strategy_number"]) '
                                                                  'is None and '
                                                                  'payload["strategy"]["strategy_number"] '
                                                                  'not in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, '
                                                                  '12, 13, 14, 15, 16, 17, 18, 19, 20, 21, '
                                                                  '22, 23, 24, 25, 26, 27, 28, 29, 30, 31, '
                                                                  '32, 33, 34, 35, 36, 37, 38, 39, 40, 41, '
                                                                  '42, 46, 47, 48, 49, 50, 51, 52, 53, 54, '
                                                                  '55, 56, 57, 58, 59, 60, 61, 64, 65, 66, '
                                                                  '68, 69, 70, 71, 72, 73, 74)):\n'
                                                                  '        raise ValueError("Numbered '
                                                                  'publisher requires sealed Strategy 2, 3, '
                                                                  '4, 5, 6, 7, 8, 9, 10, 11 or 12")\n'
                                                                  '    '
                                                                  '_validate_strategy_two_payload(payload)\n'
                                                                  '    manifest = '
                                                                  'payload["strategy"]["numbered_release"]\n'
                                                                  '    source_prefix = '
                                                                  "'fixed-structural-lots-from' if "
                                                                  "declared_fixed_structural_lot_contract(payload['strategy']['strategy_number']) "
                                                                  'is not None else {2: "strategy-two-from", '
                                                                  '3: "strategy-three-from", 4: '
                                                                  '"strategy-four-from", 5: '
                                                                  '"strategy-five-from", 6: '
                                                                  '"strategy-six-from", 7: '
                                                                  '"strategy-seven-from", 8: '
                                                                  '"strategy-eight-from", 9: '
                                                                  '"strategy-nine-from", 10: '
                                                                  '"strategy-ten-from", 11: '
                                                                  '"strategy-eleven-from", 12: '
                                                                  '"strategy-twelve-from", 13: '
                                                                  '"strategy-thirteen-from", 14: '
                                                                  '"strategy-fourteen-from", 15: '
                                                                  '"strategy-fifteen-from", 16: '
                                                                  '"strategy-sixteen-from", 17: '
                                                                  '"strategy-seventeen-from", 18: '
                                                                  '"strategy-eighteen-from", 19: '
                                                                  '"strategy-nineteen-from", 20: '
                                                                  '"strategy-twenty-from", 21: '
                                                                  '"strategy-twenty-one-from", 22: '
                                                                  '"strategy-twenty-two-from", 23: '
                                                                  '"strategy-twenty-three-from", 24: '
                                                                  '"strategy-twenty-four-from", 25: '
                                                                  '"strategy-twenty-five-from", 26: '
                                                                  '"strategy-twenty-six-from", 27: '
                                                                  '"strategy-twenty-seven-from", 28: '
                                                                  '"strategy-twenty-eight-from", 29: '
                                                                  '"strategy-twenty-nine-from", 30: '
                                                                  '"strategy-thirty-from", 31: '
                                                                  '"strategy-thirty-one-from", 32: '
                                                                  '"strategy-thirty-two-from", 33: '
                                                                  '"strategy-thirty-three-from", 34: '
                                                                  '"strategy-thirty-four-from", 35: '
                                                                  '"strategy-thirty-five-from", 36: '
                                                                  '"strategy-thirty-six-from", 37: '
                                                                  '"strategy-thirty-seven-from", 38: '
                                                                  '"strategy-thirty-eight-from", 39: '
                                                                  '"strategy-thirty-nine-from", 40: '
                                                                  '"strategy-forty-from", 41: '
                                                                  '"strategy-forty-one-from", 42: '
                                                                  '"strategy-forty-two-from", 46: '
                                                                  '"strategy-forty-six-from", 47: '
                                                                  '"strategy-forty-seven-from", 48: '
                                                                  '"strategy-forty-eight-from", 49: '
                                                                  '"strategy-forty-nine-from", 50: '
                                                                  '"strategy-fifty-from", 51: '
                                                                  '"strategy-fifty-one-from", 52: '
                                                                  "'strategy-fifty-two-from', 53: "
                                                                  '"strategy-fifty-three-from", 54: '
                                                                  '"strategy-fifty-four-from", 55: '
                                                                  "'strategy-fifty-five-from', 56: "
                                                                  "'strategy-fifty-six-from', 57: "
                                                                  "'strategy-fifty-seven-from', 58: "
                                                                  "'strategy-fifty-eight-from', 59: "
                                                                  "'strategy-fifty-nine-from', 60: "
                                                                  "'strategy-sixty-from', 61: "
                                                                  "'strategy-sixty-one-from', 64: "
                                                                  "'strategy-sixty-four-from', 65: "
                                                                  "'strategy-sixty-five-from', 66: "
                                                                  "'strategy-sixty-six-from', 68: "
                                                                  "'strategy-sixty-eight-from', 69: "
                                                                  "'strategy-sixty-nine-from', 70: "
                                                                  "'strategy-seventy-from', 71: "
                                                                  "'strategy-seventy-one-from', 72: "
                                                                  "'strategy-seventy-two-from', 73: "
                                                                  "'strategy-seventy-three-from', 74: "
                                                                  '\'strategy-seventy-four-from\'}[payload["strategy"]["strategy_number"]]\n'
                                                                  '    if (envelope["source_candidate_id"] '
                                                                  '!= '
                                                                  'f"{source_prefix}:{manifest[\'source_revision_id\']}"\n'
                                                                  '            or '
                                                                  'envelope["source_candidate_hash"] != '
                                                                  'manifest["source_payload_hash"]):\n')]},
 'backend/backtest_fixed_v4_certification.py': {'current_ast': '9328f19a53812e9a27d4dc1f5ccfe3dab17fbada403606a4c3cc07ede89649ad',
                                                'parent_ast': 'bbc483e2e44b8f2c36191cccfe3793969a1d1f37baa892e982485c4e64439c6f',
                                                'edits': [('def '
                                                           'certify_numbered_fixed_v4_projection(strategy_number: '
                                                           'int) -> str:\n'
                                                           '    """Extend the full inventory proof with '
                                                           'Strategy 2\'s explicit session lane."""\n'
                                                           '    from '
                                                           'src.trading_runtime.declared_native_manifest '
                                                           'import registered_manifest_authority\n'
                                                           '    authority = '
                                                           'registered_manifest_authority(strategy_number)\n'
                                                           '    if authority is not None:\n'
                                                           '        from '
                                                           'src.trading_runtime.strategy_registry import '
                                                           'numbered_strategy, fixed_strategy_executor\n'
                                                           '        from '
                                                           'src.trading_runtime.numbered_fixed_strategy '
                                                           'import declared_automatic_ladder_release\n'
                                                           '        if not '
                                                           'declared_automatic_ladder_release(strategy_number):\n'
                                                           "            raise ValueError('Native waiting "
                                                           "manifest lacks declared automatic semantics')\n"
                                                           '        parent_proof = '
                                                           'certify_numbered_fixed_v4_projection(authority.parent_number)\n'
                                                           '        additional_proof = '
                                                           'authority.certify_source()\n'
                                                           '        release = '
                                                           'numbered_strategy(strategy_number)\n'
                                                           '        release.verify()\n'
                                                           '        '
                                                           'fixed_strategy_executor(release.executor_strategy_id, '
                                                           'release.executor_revision).verify()\n'
                                                           '        return sha256(json.dumps((parent_proof, '
                                                           'additional_proof, release.approved_digest),\n'
                                                           '                                 '
                                                           "separators=(',', ':')).encode()).hexdigest()\n"
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_configuration '
                                                           'import declared_fixed_structural_lot_contract\n'
                                                           '    selected_lots = '
                                                           'declared_fixed_structural_lot_contract(strategy_number)\n',
                                                           'def '
                                                           'certify_numbered_fixed_v4_projection(strategy_number: '
                                                           'int) -> str:\n'
                                                           '    """Extend the full inventory proof with '
                                                           'Strategy 2\'s explicit session lane."""\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_configuration '
                                                           'import declared_fixed_structural_lot_contract\n'
                                                           '    selected_lots = '
                                                           'declared_fixed_structural_lot_contract(strategy_number)\n'),
                                                          ('def '
                                                           '_reviewed_fixed_lot_configuration_projection(source: '
                                                           'str, name: str, expected: str) -> bool:\n'
                                                           '    """Pin this exact registration delta and '
                                                           'prove whole legacy module restoration."""\n'
                                                           '    from '
                                                           '.backtest_declared_waiting_ladder_compatibility '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_waiting\n'
                                                           '    source = restore_waiting(source, '
                                                           "'backend/backtest_strategy_one_configuration.py')\n"
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v9 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v9\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v10 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v10\n',
                                                           'def '
                                                           '_reviewed_fixed_lot_configuration_projection(source: '
                                                           'str, name: str, expected: str) -> bool:\n'
                                                           '    """Pin this exact registration delta and '
                                                           'prove whole legacy module restoration."""\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v9 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v9\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v10 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v10\n'),
                                                          ('    """Apply only exact reviewed AST edits; '
                                                           'complete retained legacy pin remains '
                                                           'required."""\n'
                                                           '    supplied_source = source\n'
                                                           '    from '
                                                           '.backtest_declared_waiting_ladder_compatibility '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_waiting\n'
                                                           '    source = restore_waiting(source, relative)\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v19 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v19\n'
                                                           '    source = restore_v19(source, relative)\n',
                                                           '    """Apply only exact reviewed AST edits; '
                                                           'complete retained legacy pin remains '
                                                           'required."""\n'
                                                           '    supplied_source = source\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v19 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v19\n'
                                                           '    source = restore_v19(source, relative)\n'),
                                                          ('    """Retain core source pins under the same '
                                                           'bounded, independently reviewed projection."""\n'
                                                           '    relative = relative.removeprefix("src/")\n'
                                                           '    from '
                                                           '.backtest_declared_waiting_ladder_compatibility '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_waiting\n'
                                                           '    source = restore_waiting(source, relative)\n'
                                                           '    return ((relative == '
                                                           '"backend/backtest_strategy_one_configuration.py"\n'
                                                           '             and '
                                                           '_reviewed_fixed_lot_configuration_projection(source, '
                                                           'name, expected))\n',
                                                           '    """Retain core source pins under the same '
                                                           'bounded, independently reviewed projection."""\n'
                                                           '    relative = relative.removeprefix("src/")\n'
                                                           '    return ((relative == '
                                                           '"backend/backtest_strategy_one_configuration.py"\n'
                                                           '             and '
                                                           '_reviewed_fixed_lot_configuration_projection(source, '
                                                           'name, expected))\n')]}}
APPROVED_METADATA_ANCHOR = 'bd1dbcea84f69204b5bf666fc59fa125ffdb01fdef72738012970bce71c3eb0e'
APPROVED_SELF_AST = 'b48de4ae4a759455f7ec2b3f133aa3a3a5c70c45ccd7ab1702fae72fd7c0dbdb'

def restore_reviewed_parent_source(source, relative):
    own = Path(__file__)
    fresh_source = own.read_text(encoding='utf-8')
    tree = ast.parse(fresh_source)
    names = ('REVIEWED_EDITS', 'APPROVED_METADATA_ANCHOR', 'APPROVED_SELF_AST')
    fresh = {}
    for name in names:
        declarations = [n for n in tree.body if type(n) is ast.Assign and len(n.targets) == 1
                        and type(n.targets[0]) is ast.Name and n.targets[0].id == name]
        if len(declarations) != 1:
            raise ValueError('Waiting compatibility declaration shape differs')
        fresh[name] = ast.literal_eval(declarations[0].value)
    if fresh != dict(zip(names, (REVIEWED_EDITS, APPROVED_METADATA_ANCHOR, APPROVED_SELF_AST))):
        raise ValueError('Waiting compatibility loaded and fresh metadata differs')
    for node in tree.body:
        if type(node) is ast.Assign and len(node.targets) == 1 and type(node.targets[0]) is ast.Name:
            if node.targets[0].id in names:
                node.value = ast.Constant(None)
    if sha256(ast.unparse(tree).encode()).hexdigest() != APPROVED_SELF_AST:
        raise ValueError('Waiting compatibility envelope differs')
    if sha256(json.dumps(REVIEWED_EDITS, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != APPROVED_METADATA_ANCHOR:
        raise ValueError('Waiting compatibility metadata anchor differs')
    recipe = REVIEWED_EDITS.get(relative.removeprefix('src/'))
    if recipe is None:
        if own.read_text(encoding='utf-8') != fresh_source:
            raise ValueError('Waiting compatibility changed during restoration')
        return source
    if sha256(ast.unparse(ast.parse(source)).encode()).hexdigest() != recipe['current_ast']:
        if own.read_text(encoding='utf-8') != fresh_source:
            raise ValueError('Waiting compatibility changed during restoration')
        return source
    restored = source
    for current, previous in recipe['edits']:
        if not current or restored.count(current) != 1:
            raise ValueError('Waiting compatibility exact reviewed edit differs: ' + relative)
        restored = restored.replace(current, previous, 1)
    if sha256(ast.unparse(ast.parse(restored)).encode()).hexdigest() != recipe['parent_ast']:
        raise ValueError('Waiting compatibility retained parent AST differs: ' + relative)
    if own.read_text(encoding='utf-8') != fresh_source:
        raise ValueError('Waiting compatibility changed during restoration')
    return restored
