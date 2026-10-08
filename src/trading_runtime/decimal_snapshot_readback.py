"""Exact Decimal text readback; domain and ownership validation remain separate."""
import re

RULE = 'canonical-decimal-snapshot-readback@1'
_TEXT = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z')
_DECIMAL = re.compile(r'Decimal\(\s*([0-9]+)\s*,\s*([0-9]+)\s*\)\Z')


def canonical_decimal_row(contract, row):
    if type(row) is not dict or set(row) != {name for name, _ in contract.columns}:
        raise ValueError('Decimal readback column inventory differs')
    result = dict(row)
    for name, kind in contract.columns:
        nullable = kind.startswith('Nullable(') and kind.endswith(')')
        scalar = kind[9:-1] if nullable else kind
        match = _DECIMAL.fullmatch(scalar)
        if match is None:
            if scalar.startswith('Decimal'):
                raise ValueError('Unsupported declared decimal readback type')
            continue
        precision, scale = map(int, match.groups())
        if not 1 <= precision <= 76 or not 0 <= scale <= precision:
            raise ValueError('Decimal readback precision or scale differs')
        value = row[name]
        if value is None and nullable:
            continue
        if type(value) is not str or _TEXT.fullmatch(value) is None:
            raise ValueError('Decimal readback requires exact plain text, never floats')
        negative = value.startswith('-')
        unsigned = value[1:] if negative else value
        integer, _, fraction = unsigned.partition('.')
        if len(integer.lstrip('0')) > precision - scale or len(fraction) > scale:
            raise ValueError('Decimal readback exceeds declared precision or scale')
        result[name] = ('-' if negative else '') + integer
        if scale:
            result[name] += '.' + fraction.ljust(scale, '0')
    return result


def declared_decimal_rows(source, contract, rows):
    from src.backend.backtest_fixed_structural_lot_source import require_native_fixed_structural_lot_source
    from .strategy_registry import numbered_strategy
    require_native_fixed_structural_lot_source(source)
    source.require_installed_admission()
    strategy = source.installed_payload['strategy']
    release = numbered_strategy(strategy['strategy_number'])
    if strategy['numbered_release']['contract'] != release.canonical_payload():
        raise ValueError('Decimal readback differs from installed release')
    count = release.rule_set_contracts.count(RULE)
    if count == 0:
        return rows
    if count != 1:
        raise ValueError('Decimal readback needs one exact declared rule')
    return tuple(canonical_decimal_row(contract, row) for row in rows)


def declared_decimal_row(source, contract, row):
    return declared_decimal_rows(source, contract, (row,))[0]
