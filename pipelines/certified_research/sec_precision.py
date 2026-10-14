"""Exact bounded XBRL numeric interval assessment; never selects a reported value."""
from decimal import Decimal,InvalidOperation,localcontext
from fractions import Fraction
import re
from collections import defaultdict,Counter

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


def inventory(extraction):
    """Assess every original semantic group; dimensional facts remain included."""
    body=dict(extraction);sealed=body.pop('extraction_hash',None)
    if sealed!=digest(body) or len(extraction['observations'])+len(extraction['rejected'])>10000:
        raise ValueError('Invalid bounded original precision extraction')
    grouped=defaultdict(list);positions=set()
    for row in extraction['observations']:
        position=row['source_position']
        if position in positions:raise ValueError('Original precision inventory has duplicate positions')
        positions.add(position)
        key=(row['concept'],row['unit'],row['period_start'],row['period_end'],tuple(tuple(v) for v in row['dimensions']))
        grouped[key].append(dict(source_position=position,value=row['value'],decimals=row['decimals']))
    groups=[];counts={mode:Counter() for mode in ('round-to-nearest','truncation')}
    for key,rows in grouped.items():
        modes={}
        for mode in counts:
            try:result=assess(tuple(rows),mode=mode)
            except ValueError as error:result=dict(status='unqualified_precision',reason=str(error),certified=False)
            modes[mode]=result;counts[mode][result['status']]+=1
        groups.append(dict(context_hash=digest(key),positions=tuple(r['source_position'] for r in rows),
            dimensioned=bool(key[-1]),observations=len(rows),modes=modes))
    groups.sort(key=lambda row:row['context_hash'])
    result=dict(version='original-sec-precision-inventory-v1',extraction_hash=sealed,groups=tuple(groups),
        observations=len(positions),semantic_groups=len(groups),duplicate_groups=sum(g['observations']>1 for g in groups),
        dimensioned_groups=sum(g['dimensioned'] for g in groups),rejected_numeric_facts=len(extraction['rejected']),
        mode_status_counts={mode:dict(sorted(values.items())) for mode,values in counts.items()},certified=False)
    return dict(result,inventory_hash=digest(result))
