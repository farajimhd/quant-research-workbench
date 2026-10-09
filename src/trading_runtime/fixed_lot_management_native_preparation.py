"""Explicit installed management preparation capability, independent of identity."""
INPUT = 'declared-fixed-lot-management-native-preparation@1'
RULE = 'fixed-lot-management-native-preparation@1'


def uses_management_native_preparation(release):
    release.verify()
    inputs = release.input_contracts.count(INPUT)
    rules = release.rule_set_contracts.count(RULE)
    if not inputs and not rules:
        return False
    if inputs != 1 or rules != 1:
        raise ValueError('Management native preparation requires exact paired declarations')
    return True
