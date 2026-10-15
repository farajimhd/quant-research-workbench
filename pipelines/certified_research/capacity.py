"""Bounded physical ClickHouse storage evidence; never invent projection rates."""
from __future__ import annotations

from .contracts import identifier,literal,digest
from dataclasses import dataclass
from decimal import Decimal,ROUND_CEILING
import re

READ_PRODUCTS=frozenset(('condition_rules','calculation_recipe','canonical_source','technical_seed','v7_seed','sec_acquisition',
    'technical_origin','v7_origin','certified_bars',
    'short_volume_acquisition','short_interest_acquisition','corporate_actions','float_allocation','reported_supply'))
WRITE_PRODUCTS=frozenset(('market_aggregate','technical','v7','activity','universe','fundamentals',
    'derived_fundamentals','regular_prices','float_supply','short_volume','short_interest','short_pressure',
    'identity_observation','reference_import','v7_backfill','reference_availability'))


def plan_demands(config,plans):
    """Conservative full-plan writes, including controller metadata.

    Read-only prerequisite roots still occupy immutable campaign metadata and
    progress rows. Unknown products must be classified before admission rather
    than being silently omitted. Per-product peak bounds may overlap; summing
    them is intentionally conservative and does not assume verified reuse.
    """
    from .execution import validate
    plans=tuple(plans)
    if not plans:raise ValueError('Capacity admission requires a nonempty execution plan')
    validate(tuple(p.unit for p in plans),config.global_memory_bytes)
    counts={};minimum={};unknown={}
    for plan in plans:
        product=plan.unit.product
        if product in READ_PRODUCTS:continue
        if product not in WRITE_PRODUCTS:
            unknown[product]=unknown.get(product,0)+1;continue
        # Backfill writes the same V7 schema through the same causal producer.
        # Charge it to the independently qualified V7 storage/peak profile.
        product='v7' if product=='v7_backfill' else product
        counts[product]=counts.get(product,0)+1
        minimum[product]=min(minimum.get(product,plan.unit.memory_bytes),plan.unit.memory_bytes)
    counts['campaign_metadata']=len(plans)
    active={product:min(units,config.workers,
        config.global_memory_bytes//minimum[product] if minimum.get(product) else config.workers)
        for product,units in counts.items()}
    # Controller progress/plan publication is one scheduler-owned writer.
    active['campaign_metadata']=1
    result=dict(ready=not unknown,demands=dict(sorted(counts.items())),active_units=active,
        unknown_products=dict(sorted(unknown.items())),mode='full_plan_no_reuse_discount')
    result['demand_hash']=digest(result)
    return result


class PlanCapacityGuard:
    """Qualified pilot/storage admission bound to the controller's whole plan."""
    def __init__(self,query,config,rates,recipe_hashes,*,reserve_bytes,volume_groups,safety_factor='1.25'):
        self.query=query;self.config=config;self.rates=tuple(rates);self.recipes=dict(recipe_hashes)
        self.reserves=dict(reserve_bytes);self.volumes=dict(volume_groups);self.factor=safety_factor

    def __call__(self,plan_hash,plans):
        if not re.fullmatch('[0-9a-f]{64}',plan_hash):raise ValueError('Invalid capacity execution-plan fingerprint')
        demand=plan_demands(self.config,plans)
        storage=inventory(self.query,self.config.database)
        result=admission(storage,demand['demands'],self.rates,self.recipes,
            active_units=demand['active_units'],reserve_bytes=self.reserves,volume_groups=self.volumes,
            safety_factor=self.factor)
        if demand['unknown_products']:
            result['blockers']+=['Unclassified execution product: '+p for p in demand['unknown_products']]
            result['status']='blocked'
        result.pop('certificate_hash')
        result.update(plan_hash=plan_hash,demand_hash=demand['demand_hash'],projection_mode=demand['mode'],
            unknown_products=demand['unknown_products'])
        result['certificate_hash']=digest(result)
        return result


class CollectionCapacityGuard:
    """Sum retained outputs and cohort transient allowances.

    Loads one immutable child graph at a time. No reuse discount or arbitrary
    old-table byte rate can authorize the complete collection. Background merges
    can outlive a cohort; without a verified drain barrier their peak allowances
    are summed rather than assuming only the current cohort occupies disk.
    """
    def __init__(self, query, database, load_plan, rates, recipe_hashes, *,
                 reserve_bytes, volume_groups, safety_factor='1.25'):
        self.query=query;self.database=identifier(database);self.load_plan=load_plan
        self.rates=tuple(rates);self.recipes=dict(recipe_hashes)
        self.reserves=dict(reserve_bytes);self.volumes=dict(volume_groups);self.factor=safety_factor

    def __call__(self, frozen):
        from dataclasses import replace
        from .collection import Controller
        Controller(frozen,None,None,None,None)  # Validate the whole immutable partition.
        totals={};active={};unknown={};members=[]
        for member in frozen['members']:
            saved=self.load_plan(member['dataset_id'])
            if (saved.dataset_id!=member['dataset_id'] or saved.plan_hash!=member['plan_hash']
                    or saved.config.database!=self.database
                    or tuple(saved.config.issuer_keys)!=tuple(member['issuer_keys'])
                    or replace(saved.config,issuer_keys=()).content_hash!=frozen['partition']['config_hash']):
                raise ValueError('Collection capacity child differs from the frozen partition')
            demand=plan_demands(saved.config,saved.units)
            for product,count in demand['demands'].items():
                totals[product]=totals.get(product,0)+count
                active[product]=active.get(product,0)+demand['active_units'][product]
            for product,count in demand['unknown_products'].items():
                unknown[product]=unknown.get(product,0)+count
            members.append((saved.dataset_id,saved.plan_hash,demand['demand_hash']))
        # Header, member rows and final seal have their own measured metadata rate.
        totals['collection_metadata']=len(members)+2;active['collection_metadata']=1
        storage=inventory(self.query,self.database)
        result=admission(storage,totals,self.rates,self.recipes,active_units=active,
            reserve_bytes=self.reserves,volume_groups=self.volumes,safety_factor=self.factor)
        if unknown:
            result['blockers']+=['Unclassified execution product: '+p for p in sorted(unknown)]
            result['status']='blocked'
        result.pop('certificate_hash')
        result.update(collection_hash=frozen['collection_hash'],
            demand_hash=digest(dict(members=members,demands=totals,active_units=active)),
            projection_mode='all_retained_outputs_sum_cohort_transient_no_reuse_discount',
            unknown_products=unknown)
        result['certificate_hash']=digest(result)
        return result


@dataclass(frozen=True)
class PilotRate:
    product: str
    recipe_hash: str
    evidence_hash: str
    units: int
    persisted_bytes: int
    staging_peak_bytes: int
    merge_peak_bytes: int
    peak_active_units: int
    policy: str='live_market_ssd'

    def __post_init__(self):
        identifier(self.product)
        if (self.policy not in ('live_market_ssd','sip_raw_ssd')
                or any(not re.fullmatch('[0-9a-f]{64}',v) for v in (self.recipe_hash,self.evidence_hash))
                or type(self.units) is not int or self.units<=0
                or type(self.peak_active_units) is not int or not 1<=self.peak_active_units<=self.units
                or any(type(v) is not int or v<0 for v in (self.persisted_bytes,self.staging_peak_bytes,self.merge_peak_bytes))):
            raise ValueError('Pilot rates require qualified hashes, explicit workload policy and exact byte counts')


def admission(storage,demands,rates,recipe_hashes,*,active_units,reserve_bytes,volume_groups,safety_factor='1.25',minimum_persisted_bytes=None):
    """Project from compatible measured product units; missing rates block.

    Units are the pilot's declared comparable ticker-sessions/reference units,
    not arbitrary physical row counts from a legacy table. Pilot certification
    and representative-stratum selection remain the caller's admission duties.
    """
    factor=Decimal(safety_factor)
    if not factor.is_finite() or factor<1:raise ValueError('Capacity safety factor must be finite and at least one')
    policies=('live_market_ssd','sip_raw_ssd')
    if set(volume_groups)!=set(policies) or any(not isinstance(v,str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}',v) for v in volume_groups.values()):
        raise ValueError('Explicit physical-volume grouping is required for both workload disks')
    if any(type(reserve_bytes.get(p)) is not int or reserve_bytes[p]<0 for p in policies):
        raise ValueError('Explicit nonnegative reserve bytes are required for both workload disks')
    rates=tuple(rates);by_product={r.product:r for r in rates}
    if len(by_product)!=len(rates):raise ValueError('Competing pilot rates require explicit selection')
    minimum_persisted_bytes={} if minimum_persisted_bytes is None else dict(minimum_persisted_bytes)
    if set(minimum_persisted_bytes)-set(demands) or any(type(v) is not int or v<0 for v in minimum_persisted_bytes.values()):
        raise ValueError('Known persisted byte envelopes must be nonnegative and scoped to admitted products')
    rows=[];blocked=[];totals={p:0 for p in policies}
    for product,units in sorted(demands.items()):
        identifier(product)
        concurrent=active_units.get(product)
        if type(units) is not int or units<0 or type(concurrent) is not int or not 0<=concurrent<=units:
            raise ValueError('Capacity demands/concurrency must use explicit bounded unit counts')
        if units==0:continue
        rate=by_product.get(product)
        if rate is None:
            blocked.append(product+': qualified pilot rate is missing');continue
        if rate.recipe_hash!=recipe_hashes.get(product):
            blocked.append(product+': pilot recipe is incompatible');continue
        def rounded(value):return int(value.to_integral_value(rounding=ROUND_CEILING))
        pilot_persisted=rounded(Decimal(rate.persisted_bytes)*units/rate.units*factor)
        known_persisted=rounded(Decimal(minimum_persisted_bytes.get(product,0))*factor)
        persisted=max(pilot_persisted,known_persisted)
        transient=rounded(Decimal(rate.staging_peak_bytes+rate.merge_peak_bytes)*concurrent/rate.peak_active_units*factor)
        required=persisted+transient
        totals[rate.policy]+=required
        rows.append(dict(product=product,units=units,active_units=concurrent,policy=rate.policy,
            persisted_bytes=persisted,transient_bytes=transient,required_bytes=required,pilot_evidence=rate.evidence_hash,
            pilot_persisted_bytes=pilot_persisted,known_persisted_envelope_bytes=known_persisted))
    disks={r['name']:r for r in storage['disks']}
    if len(disks)!=len(storage['disks']):raise ValueError('Storage inventory contains duplicate workload disk names')
    headroom={}
    for policy in policies:
        disk=disks.get(policy)
        if disk is None:blocked.append(policy+': workload disk is absent');continue
        available=min(int(disk['free_space']),int(disk['unreserved_space']))-reserve_bytes[policy]
        headroom[policy]=dict(required_bytes=totals[policy],available_bytes=max(0,available))
        if totals[policy]>available:blocked.append(policy+': projected writes exceed reserved headroom')
    volumes={}
    for group in sorted(set(volume_groups.values())):
        members=[p for p in policies if volume_groups[p]==group]
        if any(p not in disks for p in members):continue
        required=sum(totals[p] for p in members)
        available=min(min(int(disks[p]['free_space']),int(disks[p]['unreserved_space'])) for p in members)-sum(reserve_bytes[p] for p in members)
        volumes[group]=dict(policies=members,required_bytes=required,available_bytes=max(0,available))
        if required>available:blocked.append(group+': combined workload writes exceed physical-volume headroom')
    result=dict(status='blocked' if blocked else 'admitted',products=rows,headroom=headroom,
        volumes=volumes,blockers=blocked,storage_evidence=storage['evidence_hash'],safety_factor=str(factor),reserve_bytes=reserve_bytes)
    result['certificate_hash']=digest(result)
    return result


def inventory(query,database='arte'):
    identifier(database)
    tables=query('SELECT database,table,sum(rows) rows,sum(data_compressed_bytes) compressed_bytes,'
        'sum(data_uncompressed_bytes) uncompressed_bytes,sum(bytes_on_disk) disk_bytes,count() parts '
        f'FROM system.parts WHERE active AND (database={literal(database)} OR '
        "(database='market_sip_compact' AND match(table,'^events_[0-9]{4}$'))) "
        'GROUP BY database,table ORDER BY database,table')
    disks=query("SELECT name,total_space,free_space,unreserved_space FROM system.disks "
        "WHERE name IN ('live_market_ssd','sip_raw_ssd') ORDER BY name")
    for row in tables:
        for field in ('rows','compressed_bytes','uncompressed_bytes','disk_bytes','parts'):row[field]=int(row[field])
        row['compressed_bytes_per_row']=row['compressed_bytes']/row['rows'] if row['rows'] else None
        row['compression_ratio']=row['uncompressed_bytes']/row['compressed_bytes'] if row['compressed_bytes'] else None
    for row in disks:
        for field in ('total_space','free_space','unreserved_space'):row[field]=int(row[field])
    result=dict(tables=tables,disks=disks,projection_ready=False,
        limitation='Physical inventory only. Campaign projection requires qualified product/session pilot rates, staging and merge headroom; old table rates are not equivalent to new normalized schemas.')
    result['evidence_hash']=digest(result)
    return result
