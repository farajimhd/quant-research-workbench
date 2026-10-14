"""Compact frozen runtime metadata in existing immutable campaign units."""
import json
from datetime import date

from .campaign import PlannedUnit,encoded
from .execution import Unit
from .contracts import digest
from . import recipe,facts,fundamentals,reference_sql,reference_producer,float_data,short_sql,short_pressure,price_inputs,activity,sec_coverage


def reference_contract():
    from . import reference_import,genesis
    modules=(reference_sql,reference_producer,float_data,short_sql,short_pressure,price_inputs,activity,sec_coverage,reference_import,genesis)
    return dict(catalog=facts.catalog_hash(),formulas=fundamentals.contract_hash(),
        algorithms={module.__name__:recipe.code_hash(module) for module in modules})


def units(config,definition,rules):
    """Keep each metadata row within the existing 8 KiB argument bound."""
    rules=json.loads(encoded(tuple(rules)));definition=json.loads(encoded(definition))
    fingerprint=recipe.verify_definition(definition,rules)
    if tuple(definition['frames'])!=config.resolutions_ms:raise ValueError('Frozen recipe frames differ from launch selection')
    chunks=[]
    for offset in range(0,len(rules),32):
        chunks.append(PlannedUnit(Unit('recipe_rules:'+str(offset//32),'condition_rules'),dict(rows=rules[offset:offset+32])))
    compact=dict(definition);compact.pop('rules')
    descriptor=PlannedUnit(Unit('calculation_recipe','calculation_recipe',tuple(p.unit.key for p in chunks)),
        dict(definition=compact,recipe_hash=fingerprint,calculation_hash=config.calculation_hash,
            rules_hash=digest(rules),reference_contract=reference_contract()))
    return (*chunks,descriptor)


def resolve(config,plans):
    plans=tuple(plans);by_key={p.unit.key:p for p in plans}
    descriptors=[p for p in plans if p.unit.product=='calculation_recipe']
    if len(by_key)!=len(plans) or len(descriptors)!=1:raise ValueError('Exactly one frozen calculation descriptor is required')
    descriptor=descriptors[0];args=descriptor.arguments
    if set(args)!={'definition','recipe_hash','calculation_hash','rules_hash','reference_contract'}:
        raise ValueError('Frozen calculation descriptor fields differ')
    if 'rules' in args['definition']:raise ValueError('Frozen rule arrays must use the bounded chunk authority')
    rules=[]
    for key in descriptor.unit.parents:
        chunk=by_key.get(key)
        if chunk is None or chunk.unit.product!='condition_rules' or chunk.unit.parents or set(chunk.arguments)!={'rows'}:
            raise ValueError('Frozen condition-rule chunk is missing or incompatible')
        rows=chunk.arguments['rows']
        if not isinstance(rows,(list,tuple)) or not 1<=len(rows)<=32:raise ValueError('Condition-rule chunk exceeds its bounded contract')
        rules.extend(rows)
    if ({p.unit.key for p in plans if p.unit.product=='condition_rules'}!=set(descriptor.unit.parents)
            or digest(rules)!=args['rules_hash']):raise ValueError('Frozen condition rules are incomplete or changed')
    definition=dict(args['definition']);definition['rules']=recipe.market_recipe(config,rules)['definition']['rules']
    fingerprint=recipe.verify_definition(definition,rules)
    if (fingerprint!=args['recipe_hash'] or args['calculation_hash']!=config.calculation_hash
            or tuple(definition['frames'])!=config.resolutions_ms or args['reference_contract']!=reference_contract()):
        raise ValueError('Frozen calculation/reference contract differs from current runtime')
    return dict(definition=definition,rules=tuple(rules),recipe_hash=fingerprint,descriptor=descriptor,
        output_hash=digest(args))


def runtime(saved,claim,client_factory,source_guard):
    """Recreate runtime from a stored plan; never substitute CLI defaults."""
    from .market_runtime import MarketRuntime
    frozen=resolve(saved.config,saved.units);sources={}
    for plan in saved.units:
        if plan.unit.product!='canonical_source':continue
        if set(plan.arguments)!={'day','source_hash'}:raise ValueError('Frozen canonical source unit fields differ')
        day=date.fromisoformat(plan.arguments['day'])
        if day in sources:raise ValueError('Duplicate frozen canonical day bindings')
        sources[day]=plan.arguments['source_hash']
    if not sources:raise ValueError('Stored runtime has no frozen canonical/reporting sources')
    return MarketRuntime(saved.dataset_id,saved.config.database,claim,client_factory,source_guard,
        frozen['definition'],frozen['rules'],sources)


class Handlers:
    def __init__(self,config,plans,claim,source_guard,market_runtime):
        self.config=config;self.plans=tuple(plans);self.claim=claim;self.guard=source_guard;self.runtime=market_runtime
    def execute(self,plan,parents,stop=None):
        if stop is not None and stop.is_set():raise InterruptedError('Calculation validation stopped')
        self.claim.check();self.guard()
        if plan.unit.product=='condition_rules':return dict(output_hash=digest(plan.arguments),rows=plan.arguments['rows'])
        frozen=resolve(self.config,self.plans)
        if (self.runtime.recipe_hash!=frozen['recipe_hash'] or self.runtime.definition!=frozen['definition']
                or self.runtime.rules!=frozen['rules']):raise ValueError('Runtime differs from frozen calculation descriptor')
        if set(parents)!=set(plan.unit.parents):raise ValueError('Calculation descriptor lacks verified rule parents')
        return dict(output_hash=frozen['output_hash'],recipe_hash=frozen['recipe_hash'])
    def producer(self,plan,parents,stop):return self.execute(plan,parents,stop)
    def verifier(self,plan,parents):return self.execute(plan,parents)
    def registry(self):
        products=('condition_rules','calculation_recipe')
        return {p:self.producer for p in products},{p:self.verifier for p in products}
