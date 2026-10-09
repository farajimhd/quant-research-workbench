"""Generic declared acquisition quota; no numbered-strategy dispatch."""

RULE = 'portfolio-single-session-acquisition-quota@1'
PARAMETER = 'session_acquisition_quota'


def declared_acquisition_limit(payload):
    """Parse an already certified complete configuration; grant no authority."""
    if type(payload) is not dict or type(payload.get('strategy')) is not dict:
        raise ValueError('Acquisition policy requires a complete strategy configuration')
    strategy = payload['strategy']
    manifest = strategy.get('numbered_release')
    if type(manifest) is not dict or type(manifest.get('contract')) is not dict:
        raise ValueError('Acquisition policy lacks its declared release contract')
    rules = manifest['contract'].get('rule_set_contracts')
    parameters = strategy.get('parameters')
    if type(rules) not in {list, tuple} or type(parameters) is not dict:
        raise ValueError('Acquisition policy declaration is incomplete')
    count = rules.count(RULE)
    declaration = parameters.get(PARAMETER)
    if count == 0:
        if PARAMETER in parameters:
            raise ValueError('Acquisition quota parameter lacks its selected rule')
        return None
    if count != 1 or type(declaration) is not dict or set(declaration) != {
            'maximum_accepted_acquisitions_per_ticker_session', 'scope'}:
        raise ValueError('Acquisition quota requires one exact declared parameter block')
    maximum = declaration['maximum_accepted_acquisitions_per_ticker_session']
    if (type(maximum) is not int or not 1 <= maximum <= 32
            or declaration['scope'] != 'independent_native_session'):
        raise ValueError('Acquisition quota has an invalid bound or session scope')
    return maximum
