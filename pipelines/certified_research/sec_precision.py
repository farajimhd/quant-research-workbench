"""Exact bounded XBRL numeric interval assessment; never selects a reported value."""
from decimal import Decimal,InvalidOperation,localcontext
from fractions import Fraction
import re

from .contracts import digest


def assess(rows,*,mode):
    if mode not in ('round-to-nearest','truncation') or not isinstance(rows,(tuple,list)) or not 1<=len(rows)<=10000:
        raise ValueError('Explicit rounding mode and bounded numeric witnesses are required')
    intervals=[];seen=set()
    for row in rows:
        value=row['value'];decimals=row['decimals'];position=row['source_position']
        if type(position) is not int or position<0 or position in seen:raise ValueError('Invalid or duplicate precision source position')
        seen.add(position)
        if not isinstance(value,str) or not 1<=len(value)<=512:raise ValueError('Precision value exceeds decimal bound')
        try:number=Decimal(value)
        except InvalidOperation as error:raise ValueError('Invalid precision decimal value') from error
        if not number.is_finite() or abs(number.as_tuple().exponent)>128:raise ValueError('Nonfinite or excessive precision exponent')
        number=Fraction(number)
        if decimals=='INF':lower=upper=number;left=right=True
        else:
            if not isinstance(decimals,str) or not re.fullmatch('[+-]?[0-9]{1,3}',decimals) or abs(int(decimals))>128:
                raise ValueError('Missing or unsupported declared decimals')
            places=int(decimals);quantum=Fraction(1,10**places) if places>=0 else Fraction(10**(-places))
            if (number/quantum).denominator!=1:raise ValueError('Digits exceed declared precision')
            if mode=='round-to-nearest':lower=number-quantum/2;upper=number+quantum/2;left=right=True
            elif number>0:lower=number;upper=number+quantum;left=True;right=False
            elif number<0:lower=number-quantum;upper=number;left=False;right=True
            else:lower=-quantum;upper=quantum;left=right=False
        intervals.append((lower,upper,left,right))
    lower=max(r[0] for r in intervals);upper=min(r[1] for r in intervals)
    left=all(r[2] for r in intervals if r[0]==lower);right=all(r[3] for r in intervals if r[1]==upper)
    compatible=lower<upper or (lower==upper and left and right)
    def text(number):
        with localcontext() as context:
            context.prec=2048
            return format(Decimal(number.numerator)/Decimal(number.denominator),'f')
    result=dict(version='xbrl-numeric-interval-assessment-v1',mode=mode,source_positions=tuple(r['source_position'] for r in rows),
        witnesses_hash=digest(rows),status='compatible_intervals' if compatible else 'disjoint_intervals',
        intersection=dict(lower=text(lower),upper=text(upper),lower_inclusive=left,upper_inclusive=right) if compatible else None,
        certified=False,value_selected=False)
    return dict(result,assessment_hash=digest(result))
