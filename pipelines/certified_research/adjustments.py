"""Causal price-state adjustment from certified corporate-action history."""
from dataclasses import dataclass
from datetime import date,datetime,time,timezone
from math import isfinite
from .contracts import digest,identifier,literal
from .reference_publication import ReferencePublisher
from .float_data import key
from .checkpoints import NY
from .indicators import validate_state


@dataclass(frozen=True)
class Adjustment:
    factor: float
    evidence_hash: str
    parent_hash: str

    def __post_init__(self):
        import re
        if (not isfinite(self.factor) or self.factor<=0
                or any(not re.fullmatch('[0-9a-f]{64}',h) for h in (self.evidence_hash,self.parent_hash))):
            raise ValueError('Invalid certified price adjustment')


def resolve(query,database,claim,scope,listing_key,previous,day,*,membership_sql=None):
    """Use executions publicly confirmed before PM; pending actions block.

    The corporate producer must certify complete history, including earlier
    announcements for future effective dates. Publication-range coverage alone
    does not establish that completeness; acquisition admission owns that proof.
    """
    identifier(database)
    if (scope.family!='corporate_actions' or scope.entity_kind!=2 or type(previous) is not date
            or type(day) is not date or previous>=day or scope.start>previous or scope.end<day
            or type(listing_key) is not int or not 0<listing_key<2**64):
        raise ValueError('Corporate adjustment history does not cover the continuation range')
    publisher=ReferencePublisher(query,database,claim);receipt=publisher.receipt(scope)
    if receipt is None:raise ValueError('Corporate adjustment history is not certified')
    cutoff=datetime.combine(day,time(4),NY).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
    membership_source=f'{database}.research_membership_v1' if membership_sql is None else f'({membership_sql})'
    identity=(f'dataset_id=toUUID({literal(scope.dataset_id)}) AND security_key={scope.entity_key} AND eligible=1 AND '
        if membership_sql is None else '')
    membership=query(f'SELECT count() n FROM {membership_source} WHERE '
        f'{identity}listing_key={listing_key} '
        f'AND valid_from<=toDateTime64({literal(cutoff)},6,\'UTC\') '
        f'AND valid_to>toDateTime64({literal(cutoff)},6,\'UTC\')')
    if len(membership)!=1 or int(membership[0]['n'])!=1:raise ValueError('Corporate security does not match dated listing membership')
    executed=key('corporate.executed_split_ratio');announced=key('corporate.announced_split_ratio')
    predicate=(f"{scope.rows_where(receipt['attempt_id'])} AND metric_key IN ({executed},{announced}) "
        f"AND available_at<=toDateTime64({literal(cutoff)},6,'UTC') "
        f'AND (period_end IS NULL OR (period_end>toDate({literal(previous)}) AND period_end<=toDate({literal(day)})))')
    conflicts=query(f'SELECT count() n FROM (SELECT metric_key,period_end,available_at,revision FROM {database}.research_facts_v1 '
        f'WHERE {predicate} GROUP BY metric_key,period_end,available_at,revision '
        'HAVING uniqExact(tuple(value,status,unit_key,basis_unit_key,taxonomy_key,context_kind))>1)')
    if int(conflicts[0]['n']):raise ValueError('Corporate vintages have conflicting ratio/context evidence')
    rows=query(f"SELECT period_end,metric_key,argMax(tuple(value,status,unit_key,basis_unit_key,taxonomy_key,context_kind,evidence_hash),"
        f"tuple(available_at,revision,evidence_hash)) observation FROM {database}.research_facts_v1 WHERE {predicate} "
        'GROUP BY period_end,metric_key ORDER BY period_end,metric_key')
    ratio=1.;by_day={}
    for row in rows: # Metadata: at most two action summaries per effective date.
        value,status,unit,basis,taxonomy,context,evidence=row['observation']
        if (row['period_end'] is None or int(status)!=1 or int(unit)!=3 or int(basis)!=3
                or int(taxonomy)!=0 or int(context)!=1 or value is None or not isfinite(float(value)) or float(value)<=0):
            raise ValueError('Corporate action has invalid or unavailable effective-date/ratio evidence')
        by_day.setdefault(row['period_end'],{})[int(row['metric_key'])]=(float(value),evidence)
    for effective,actions in by_day.items():
        if announced in actions and executed not in actions:
            raise ValueError('Announced split has no public execution confirmation before PM')
        if executed in actions:ratio*=actions[executed][0]
    factor=1./ratio
    if publisher.receipt(scope)!=receipt:raise ValueError('Corporate evidence changed during adjustment resolution')
    causal=[dict(period_end=r['period_end'],metric_key=r['metric_key'],observation=r['observation'][:6]) for r in rows]
    proof=digest(dict(version='public-price-state-adjustment-v1',listing_key=listing_key,security_key=scope.entity_key,
        previous=previous,day=day,cutoff=cutoff,actions=causal,factor=factor))
    return Adjustment(factor,proof,receipt['output_hash'])


def indicator_state(state,adjustment):
    """Scale price-valued state once; counts and RSI ratios remain invariant."""
    validate_state(state)
    result={frame:{name:value if name=='sample_count' else value*adjustment.factor for name,value in row.items()}
        for frame,row in state.items()}
    validate_state(result)
    return result
