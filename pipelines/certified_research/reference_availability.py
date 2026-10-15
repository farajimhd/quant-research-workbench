"""Compact explicit unknowns; numeric facts are published only by qualified producers."""
from .contracts import digest,literal
from .publication import insert_rows
from .independent_market import UNKNOWN_FAMILIES

TABLE='research_reference_availability_v1'


def persistence_table():
    from .schema import Table,c
    return Table(TABLE,(c('dataset_id','UUID'),c('entity_key','UInt64','T64,ZSTD(1)'),
        c('family','LowCardinality(String)'),c('range_start','Date'),c('range_end','Date'),
        c('status','UInt8'),c('reason','LowCardinality(String)'),c('evidence_hash','FixedString(64)')),
        ('dataset_id','entity_key','family','range_start','range_end'),'toYYYYMM(range_end)')


class Handlers:
    def __init__(self,dataset,config,claim,clients,guard):
        self.dataset=dataset;self.config=config;self.claim=claim;self.clients=clients;self.guard=guard

    def execute(self,plan,*,write,stop=None):
        a=plan.arguments
        if (self.config.product_scope!='market_bars' or plan.unit.product!='reference_availability'
                or set(a)!={'entity_key','start','end','families','status','reason'}
                or tuple(a['families'])!=UNKNOWN_FAMILIES or a['status']!='unknown'
                or a['start']!=str(self.config.start) or a['end']!=str(self.config.end)
                or a['reason']!='not_qualified_in_independent_market_scope'
                or type(a['entity_key']) is not int or not 0<a['entity_key']<2**64):
            raise ValueError('Invalid independent reference availability contract')
        if stop is not None and stop.is_set():raise InterruptedError('Reference availability stopped')
        self.claim.check();self.guard();client=self.clients()
        try:
            from .schema import verify_auxiliary_storage
            verify_auxiliary_storage(client.query,self.config.database,(TABLE,))
            expected=[dict(dataset_id=self.dataset,entity_key=a['entity_key'],family=f,
                range_start=a['start'],range_end=a['end'],status=0,reason=a['reason'],evidence_hash=digest(dict(a,family=f))) for f in UNKNOWN_FAMILIES]
            sql=f'SELECT * FROM {self.config.database}.{TABLE} WHERE dataset_id=toUUID({literal(self.dataset)}) AND entity_key={a["entity_key"]}'
            rows=client.query(sql)
            wanted={r['family']:r for r in expected};seen={}
            for row in rows:
                row=dict(row,entity_key=int(row['entity_key']),status=int(row['status']))
                if row['family'] in seen or wanted.get(row['family'])!=row:raise ValueError('Reference availability differs from frozen scope')
                seen[row['family']]=row
            missing=[r for r in expected if r['family'] not in seen]
            if missing:
                if not write:return None
                self.claim.check();self.guard()
                insert_rows(client.query,self.config.database,TABLE,missing)
                actual=client.query(sql)
                if sorted(actual,key=lambda r:r['family'])!=sorted(expected,key=lambda r:r['family']):
                    raise ValueError('Reference availability publication is uncertain')
            return dict(output_hash=digest(expected),rows=len(expected),status='unknown',combined_dataset_complete=False)
        finally:client.close()

    def producer(self,plan,parents,stop):return self.execute(plan,write=True,stop=stop)
    def verifier(self,plan,parents):return self.execute(plan,write=False)
    def registry(self):return {'reference_availability':self.producer},{'reference_availability':self.verifier}
