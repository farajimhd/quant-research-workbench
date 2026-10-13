"""Shared request/engine resolution from a campaign's certified dependencies."""
from datetime import date,datetime,time,timezone
from uuid import uuid4,UUID
from . import producer,market,market_context,adjustments,price_inputs,source
from .contracts import identifier,literal
from .checkpoints import NY
from .recipe import verify_definition
from .reference_publication import ReferenceScope,ReferencePublisher
from .publication import Scope,Publisher


class MarketRuntime:
    """One resolver for all dates; clients and restored engines remain private.

    First-session seed units must supply certified dependency locators. Genesis
    and rename admission are separate producers, never inferred here. Source
    hashes include that day's canonical/reporting proof, not the future range.
    """
    def __init__(self,dataset_id,database,claim,client_factory,source_guard,recipe_definition,rules,day_sources):
        UUID(dataset_id);identifier(database)
        self.dataset=dataset_id;self.database=database;self.claim=claim;self.clients=client_factory;self.guard=source_guard
        self.definition=recipe_definition;self.rules=tuple(rules);self.recipe_hash=verify_definition(recipe_definition,self.rules)
        self.sources={date.fromisoformat(str(day)):value for day,value in day_sources.items()}

    @staticmethod
    def one(parents,predicate,label):
        rows=[row for row in parents.values() if predicate(row)]
        if len(rows)!=1:raise ValueError('Exactly one certified '+label+' dependency is required')
        return rows[0]

    def corporate(self,parents,query):
        row=self.one(parents,lambda r:r.get('family')=='corporate_actions','corporate history')
        scope=ReferenceScope(str(row['dataset_id']),int(row['entity_kind']),int(row['entity_key']),'corporate_actions',
            date.fromisoformat(str(row['range_start'])),date.fromisoformat(str(row['range_end'])),row['parent_hash'],row['catalog_hash'])
        if scope.dataset_id!=self.dataset or ReferencePublisher(query,self.database,self.claim).receipt(scope)!=row:
            raise ValueError('Corporate dependency differs from the current dataset authority')
        return scope

    def request(self,plan,parents):
        client=self.clients()
        try:
            self.claim.check();self.guard();args=plan.arguments
            day=date.fromisoformat(args['day']);listing=int(args['listing_key']);ticker=args['ticker']
            source_hash=self.sources.get(day)
            if source_hash is None:raise ValueError('Market day is outside frozen canonical/reporting context')
            spelling=args.get('source_ticker')
            source.require_exact_symbol(client.query,day,ticker,source_ticker=spelling)
            unit=market.MarketUnit(self.dataset,day,ticker,listing,str(uuid4()),spelling)
            start=datetime.combine(day,time(4),NY).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
            end=datetime.combine(day,time(20),NY).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
            membership=client.query(f'SELECT count() n FROM {self.database}.research_membership_v1 WHERE '
                f'dataset_id=toUUID({literal(self.dataset)}) AND listing_key={listing} AND ticker={literal(ticker)} AND eligible=1 '
                f"AND valid_from<=toDateTime64({literal(start)},6,'UTC') AND valid_to>=toDateTime64({literal(end)},6,'UTC')")
            if len(membership)!=1 or int(membership[0]['n'])!=1:
                raise ValueError('Market execution requires unambiguous full-session dated membership')
            prior={};indicator_hash='0'*64;v7_hash='0'*64;adjustment_hash=''
            if plan.unit.product=='technical':
                parent=self.one(parents,lambda r:r.get('product') in ('technical','technical_state'),'indicator state')
                continuation=market_context.indicator_continuation(client.query,self.database,self.claim,parent,listing,day,
                    self.recipe_hash,self.sources,self.rules,self.corporate(parents,client.query))
                prior=continuation['state'];indicator_hash=continuation['predecessor_hash']
                adjustment_hash=continuation['adjustment'].evidence_hash
            elif plan.unit.product=='v7':
                parent=self.one(parents,lambda r:r.get('product')=='v7','V7 state')
                previous,context=market_context._context(parent,listing,day,self.recipe_hash,self.sources)
                if context['ticker']!=ticker:raise ValueError('Certified rename transition is required')
                adjustment=adjustments.resolve(client.query,self.database,self.claim,self.corporate(parents,client.query),listing,previous,day)
                scope=Scope(str(parent['dataset_id']),previous,listing,'v7',parent['parent_hash'])
                receipt=Publisher(client.query,self.database,self.claim).receipt(scope)
                if receipt is None or receipt['output_hash']!=parent['output_hash']:
                    raise ValueError('V7 predecessor differs from certified authority')
                rows=client.query(f'SELECT state_hash FROM {self.database}.research_v7_checkpoint_v1 WHERE '
                    +scope.where(receipt['attempt_id'])+' AND boundary=3')
                if len(rows)!=1:raise ValueError('Prior AH boundary is missing or duplicated')
                v7_hash=rows[0]['state_hash'];adjustment_hash=adjustment.evidence_hash
            elif plan.unit.product!='market_aggregate':raise ValueError('Unsupported market execution product')
            self.claim.check();self.guard()
            return producer.MarketRequest(unit,self.recipe_hash,source_hash,self.rules,prior,indicator_hash,v7_hash,
                price_inputs.calendar_close(day),not prior,self.definition,adjustment_hash)
        finally:client.close()

    def engine(self,plan,parents):
        if plan.unit.product!='v7':raise ValueError('Only V7 execution may restore a structural engine')
        client=self.clients()
        try:
            self.claim.check();self.guard();args=plan.arguments
            parent=self.one(parents,lambda r:r.get('product')=='v7','V7 state')
            authority=market_context.structural(client.query,self.database,self.claim,parent,int(args['listing_key']),
                date.fromisoformat(args['day']),args['ticker'],self.recipe_hash,self.sources,
                corporate_scope=self.corporate(parents,client.query))
            self.claim.check();self.guard();return authority
        finally:client.close()
