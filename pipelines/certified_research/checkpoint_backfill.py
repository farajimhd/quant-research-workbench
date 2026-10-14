"""Causal session-checkpoint backfill from qualified ClickHouse bars only."""
from dataclasses import replace
from datetime import date
from threading import Event
from uuid import UUID,uuid4

from . import producer,market,v7,recipe,runtime_plan,reference_import,initial_plan,adjustments,genesis
from .campaign import PlannedUnit,load_plan
from .contracts import digest,identifier,literal
from .execution import Unit,validate
from .publication import Scope,Publisher
from .reference_publication import ReferencePublisher
from .price_inputs import calendar_close

VERSION='certified-session-checkpoint-backfill-v1'


def closure(plans,roots):
    by_key={p.unit.key:p for p in plans};selected=set();pending=list(roots)
    if len(by_key)!=len(plans):raise ValueError('Backfill source plan keys are duplicated')
    while pending:
        key=pending.pop()
        if key in selected:continue
        if key not in by_key:raise ValueError('Backfill source dependency is missing')
        selected.add(key);pending.extend(by_key[key].unit.parents)
    return tuple(p for p in plans if p.unit.key in selected)


def assemble(query,saved,dataset_id,claim,guard,*,stop=None,start=None,end=None,progress=None):
    """Freeze a structural dependency closure; never rescan/copy market rows.

    The source plan's concrete recipe must still be compatible. Every needed
    bar receipt is independently checked; metadata without physical evidence
    cannot authorize backfill. Reference imports preserve original vintages.
    """
    dataset_id=str(UUID(dataset_id))
    if UUID(dataset_id)==UUID(saved.dataset_id):raise ValueError('Backfill requires a new dataset; source authority is immutable')
    stop=Event() if stop is None else stop
    def check(*,source=False):
        if stop.is_set():raise InterruptedError('Checkpoint backfill preparation stopped')
        claim.check()
        if source:guard()
    def report(phase,key,done,total,status):
        if progress is not None:
            active=int(status=='active');failed=int(status=='failed')
            progress(dict(phase=phase,unit=key,status=status,counts=dict(queued=total-done-active-failed,
                active=active,prepared=done,failed=failed,blocked=0)))
    check(source=True);frozen=runtime_plan.resolve(saved.config,saved.units)
    config=replace(saved.config,start=saved.config.start if start is None else start,
        end=saved.config.end if end is None else end)
    if not saved.config.start<=config.start<=config.end<=saved.config.end:
        raise ValueError('Backfill target range must stay within the source plan')
    roots=tuple(p.unit.key for p in saved.units if p.unit.product=='v7'
        and config.start<=date.fromisoformat(p.arguments['day'])<=config.end)
    if not roots:raise ValueError('Source plan has no target causal V7 lanes')
    # Keep all original day bindings so the unchanged source epoch remains
    # independently verifiable, including original nonstructural price context.
    extra=tuple(p.unit.key for p in saved.units if p.unit.product in ('canonical_source','calculation_recipe'))
    sources={date.fromisoformat(p.arguments['day']):p.arguments['source_hash']
        for p in saved.units if p.unit.product=='canonical_source'}
    refs={p.unit.key:p for p in saved.units};publisher=Publisher(query,saved.config.database,claim)
    reference=ReferencePublisher(query,saved.config.database,claim)
    def corporate_row(plan):
        args=plan.arguments
        if 'receipt' in args:row=args['receipt']
        else:
            if len(plan.unit.parents)!=1:raise ValueError('Corporate import source locator is ambiguous')
            parent=refs[plan.unit.parents[0]]
            if parent.unit.product!='reference_import':raise ValueError('Corporate import source is unsupported')
            row=reference.receipt(reference_import.target_scope(saved.dataset_id,parent.arguments['source_receipt']))
        if row is None:raise ValueError('Backfill corporate authority is missing')
        if reference.receipt(reference_import.source_scope(row))!=row:
            raise ValueError('Backfill corporate authority differs from physical evidence')
        return row
    # Replace each first target's previous-day calculation with a qualified AH
    # locator when available. Otherwise retain and replay its complete ancestry.
    seed_replacements={};first={}
    for key in roots:
        plan=refs[key];listing=int(plan.arguments['listing_key']);day=date.fromisoformat(plan.arguments['day'])
        if listing not in first or day<first[listing][0]:first[listing]=(day,plan)
    for index,(_,first_plan) in enumerate(first.values()):
        report('backfill-seed-selection',first_plan.unit.key,index,len(first),'active')
        check()
        previous=[refs[key] for key in first_plan.unit.parents if refs[key].unit.product=='v7']
        if not previous:
            report('backfill-seed-selection',first_plan.unit.key,index+1,len(first),'prepared')
            continue
        if len(previous)!=1:raise ValueError('Backfill predecessor plan is ambiguous')
        previous=previous[0];args=previous.arguments;day=date.fromisoformat(args['day'])
        seed=[refs[key] for key in previous.unit.parents if refs[key].unit.product in ('v7','v7_seed','v7_origin')]
        corporate=[refs[key] for key in previous.unit.parents if refs[key].unit.product=='corporate_actions']
        if len(seed)!=1 or len(corporate)!=1:raise ValueError('Backfill candidate state/adjustment parents are ambiguous')
        if seed[0].unit.product=='v7_origin':
            origin=genesis.verify(seed[0].arguments['binding'],frozen['definition'],frozen['rules'])
            if origin['origin']!=str(day) or origin['listing_key']!=int(args['listing_key']):
                raise ValueError('Backfill candidate origin differs from its listing/session')
            adjustment_hash=origin['origin_hash']
        else:
            preceding=(date.fromisoformat(seed[0].arguments['day']) if seed[0].unit.product=='v7'
                else date.fromisoformat(str(seed[0].arguments['dependency']['session_date'])))
            row=corporate_row(corporate[0]);scope=reference_import.source_scope(row)
            adjustment_hash=adjustments.resolve(query,saved.config.database,claim,scope,int(args['listing_key']),preceding,day).evidence_hash
        context=dict(source_hash=sources[day],ticker=args['ticker'],adjustment_hash=adjustment_hash)
        if 'source_ticker' in args:context['source_ticker']=args['source_ticker']
        found=v7.discover(query,saved.config.database,claim,int(args['listing_key']),
            date.fromisoformat(first_plan.arguments['day']),frozen['recipe_hash'],frozen['rules'],{day:context},dataset_id=saved.dataset_id)
        if found:
            seed_replacements[previous.unit.key]=PlannedUnit(replace(previous.unit,product='v7_seed',parents=('source:'+str(day),)),
                dict(dependency=found['dependency']))
        report('backfill-seed-selection',first_plan.unit.key,index+1,len(first),'prepared')
    base=tuple(seed_replacements.get(p.unit.key,p) for p in saved.units)
    selected=closure(base,(*roots,*extra));prepared=[]
    for index,plan in enumerate(selected): # Bounded metadata; market/fact rows stay in CH.
        report('backfill-plan-preparation',plan.unit.key,index,len(selected),'active')
        check();kind=plan.unit.product;args=plan.arguments
        if kind=='market_aggregate':
            day=date.fromisoformat(args['day']);unit=market.MarketUnit(saved.dataset_id,day,args['ticker'],
                int(args['listing_key']),str(uuid4()),args.get('source_ticker'))
            request=producer.MarketRequest(unit,frozen['recipe_hash'],sources[day],tuple(frozen['rules']),{},
                '0'*64,'0'*64,calendar_close(day),True,frozen['definition'])
            scope=Scope(saved.dataset_id,day,unit.listing_key,'bars',request.parent('bars'))
            receipt=publisher.receipt(scope)
            if receipt is None:
                report('backfill-plan-preparation',plan.unit.key,index,len(selected),'failed')
                raise ValueError(f'{plan.unit.key}: compatible certified source bars are missing')
            prepared.append(PlannedUnit(replace(plan.unit,product='certified_bars'),
                dict(args,receipt=receipt,version=VERSION,algorithm_hash=recipe.code_hash(assemble))))
        elif kind=='v7':prepared.append(PlannedUnit(replace(plan.unit,product='v7_backfill'),dict(args)))
        elif kind=='corporate_actions':
            row=corporate_row(plan)
            target=reference_import.target_scope(dataset_id,row);key=plan.unit.key+':backfill_import'
            prepared.append(PlannedUnit(Unit(key,'reference_import',memory_bytes=plan.unit.memory_bytes),dict(source_receipt=row)))
            prepared.append(PlannedUnit(replace(plan.unit,parents=(key,)),dict(entity_key=target.entity_key,
                start=str(target.start),end=str(target.end),import_parent_hash=target.parent_hash)))
        else:prepared.append(plan)
        report('backfill-plan-preparation',plan.unit.key,index+1,len(selected),'prepared')
    plans=closure(prepared,(*roots,*extra))
    allowed={'canonical_source','calculation_recipe','condition_rules','universe','v7_origin','v7_seed',
        'certified_bars','v7_backfill','corporate_actions','reference_import'}
    if any(p.unit.product not in allowed for p in plans):raise ValueError('Backfill closure contains an unsupported prerequisite')
    validate(tuple(p.unit for p in plans),config.global_memory_bytes);check(source=True)
    return dict(plans=plans,config=config,recipe_hash=frozen['recipe_hash'],source_hash=saved.source_hash,
        source_dataset_id=saved.dataset_id,preparation_hash=digest(dict(version=VERSION,source_plan=saved.plan_hash,
        target_dataset=dataset_id,units=[p.row(dataset_id) for p in plans])))


def run(query,database,source_dataset_id,dataset_id,claim,client_factory,dictionary_claim_factory,capacity_factory,*,
        stop=None,progress=None,start=None,end=None):
    """Unified launcher path; stored resume uses these same typed handlers."""
    from .verification import source_guard
    identifier(database);UUID(dataset_id);UUID(source_dataset_id)
    existing=query(f'SELECT count() n FROM {database}.research_dataset_v1 '
        f'WHERE dataset_id=toUUID({literal(dataset_id)})')
    if len(existing)!=1 or int(existing[0]['n']):raise ValueError('Backfill target already exists or is ambiguous; use resume')
    saved=load_plan(query,database,source_dataset_id);guard=source_guard(query,saved)
    assembled=assemble(query,saved,dataset_id,claim,guard,stop=stop,start=start,end=end,progress=progress)
    return initial_plan.run(query,assembled['config'],dataset_id,assembled,claim,client_factory,dictionary_claim_factory,
        capacity_factory,stop=stop,progress=progress)


class Handlers:
    def __init__(self,database,claim,clients,guard,runtime,plans):
        self.database=database;self.claim=claim;self.clients=clients;self.guard=guard;self.runtime=runtime
        self.plans={p.unit.key:p for p in plans}

    @staticmethod
    def market_plan(plan,product):
        args={k:v for k,v in plan.arguments.items() if k in ('day','listing_key','ticker','source_ticker')}
        return PlannedUnit(replace(plan.unit,product=product),args)

    def bars(self,plan):
        args=plan.arguments
        required={'day','listing_key','ticker','receipt','version','algorithm_hash'}
        if (not required<=set(args) or set(args)-required-{'source_ticker'} or args['version']!=VERSION
                or args['algorithm_hash']!=recipe.code_hash(assemble)):
            raise ValueError('Invalid frozen backfill bar locator')
        request=self.runtime.request(self.market_plan(plan,'market_aggregate'),{})
        row=args['receipt'];scope=Scope(str(row['dataset_id']),request.unit.day,request.unit.listing_key,
            'bars',request.parent('bars'));client=self.clients()
        try:
            self.claim.check();self.guard()
            if Publisher(client.query,self.database,self.claim).receipt(scope)!=row:
                raise ValueError('Backfill source bars changed or differ from their qualified receipt')
            self.claim.check();self.guard()
            return dict(receipts={'bars':row},output_hash=digest(dict(version=VERSION,bars=row)))
        finally:client.close()

    def request(self,plan,parents):
        if plan.unit.product!='v7_backfill':raise ValueError('Wrong checkpoint backfill product')
        matches=[row for key,row in parents.items() if self.plans[key].unit.product=='certified_bars']
        if len(matches)!=1:raise ValueError('Checkpoint backfill requires one certified bar dependency')
        bars=matches[0]['receipts']['bars']
        request=self.runtime.request(self.market_plan(plan,'v7'),parents)
        if (str(bars['session_date'])!=str(request.unit.day) or int(bars['listing_key'])!=request.unit.listing_key
                or bars['product']!='bars' or bars['parent_hash']!=request.parent('bars')):
            raise ValueError('Checkpoint backfill bar scope differs from frozen recipe/source identity')
        return replace(request,bars_dataset_id=str(bars['dataset_id'])),bars

    def verifier(self,plan,parents):
        if plan.unit.product=='certified_bars':return self.bars(plan)
        request,bars=self.request(plan,parents);client=self.clients()
        try:
            self.claim.check();self.guard()
            if Publisher(client.query,self.database,self.claim).receipt(Scope(request.bars_dataset_id,request.unit.day,
                    request.unit.listing_key,'bars',request.parent('bars')))!=bars:
                raise ValueError('Backfill source bars changed')
            receipt=v7.authority(request,bars,client.query,self.database,self.claim)
            if receipt is None:return None
            result,_=v7.verify_seed(client.query,self.database,self.claim,receipt,request.dataset_hash,request.source_hash,request.rules)
            self.claim.check();self.guard();return result
        finally:client.close()

    def producer(self,plan,parents,stop):
        if stop.is_set():raise InterruptedError('Checkpoint backfill stopped')
        if plan.unit.product=='certified_bars':return self.bars(plan)
        request,bars=self.request(plan,parents)
        _,handler=producer.market_execution(request,self.clients,self.claim,self.guard,
            lambda:self.runtime.engine(self.market_plan(plan,'v7'),parents),database=self.database,
            query_memory_bytes=plan.unit.memory_bytes)
        handler(Unit('v7','v7'),{'bars':bars},stop)

    def registry(self):
        products=('certified_bars','v7_backfill')
        return {p:self.producer for p in products},{p:self.verifier for p in products}
