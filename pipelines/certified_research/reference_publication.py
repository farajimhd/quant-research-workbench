"""Reference attempts are visible only after independent readback certification."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date,datetime,timezone
from uuid import UUID
import re

from .contracts import digest,identifier,literal
from .publication import evidence_sql,insert_rows
from .schema import TABLES
from .facts import FACT_KEY


@dataclass(frozen=True)
class ReferenceScope:
    dataset_id: str
    entity_kind: int
    entity_key: int
    family: str
    start: date
    end: date
    parent_hash: str
    catalog_hash: str

    def __post_init__(self):
        UUID(self.dataset_id)
        if (self.entity_kind not in (1,2,3) or type(self.entity_key) is not int or not 0<self.entity_key<2**64
                or not re.fullmatch(r'[a-z_]{1,48}',self.family) or type(self.start) is not date or type(self.end) is not date
                or self.start>self.end or any(not re.fullmatch(r'[0-9a-f]{64}',h) for h in (self.parent_hash,self.catalog_hash))):
            raise ValueError('Invalid reference publication scope')

    def rows_where(self,attempt):
        UUID(attempt)
        return (f'dataset_id=toUUID({literal(self.dataset_id)}) AND entity_kind={self.entity_kind} '
                f'AND entity_key={self.entity_key} AND attempt_id=toUUID({literal(attempt)})')

    def receipt_where(self):
        return (f'dataset_id=toUUID({literal(self.dataset_id)}) AND entity_kind={self.entity_kind} '
                f'AND entity_key={self.entity_key} AND family={literal(self.family)} '
                f'AND range_start=toDate({literal(self.start)}) AND range_end=toDate({literal(self.end)})')


class ReferencePublisher:
    def __init__(self,query,database,claim):
        self.query=query;self.database=identifier(database);self.claim=claim
        self.claim.check()

    def evidence(self,scope,attempt):
        table=next(t for t in TABLES if t.name=='research_facts_v1')
        rows=self.query(evidence_sql(table,f'SELECT * FROM {self.database}.research_facts_v1 WHERE '
                    +scope.rows_where(attempt),FACT_KEY))
        if len(rows)!=1:raise ValueError('Reference evidence is ambiguous')
        proof={k:int(v) for k,v in rows[0].items()}
        if proof['n']!=proof['unique_keys']:raise ValueError('Duplicate reference fact keys')
        bounds=self.query(f'SELECT countIf(toDate(available_at)<toDate({literal(scope.start)}) '
                         f'OR toDate(available_at)>toDate({literal(scope.end)})) bad '
                         f'FROM {self.database}.research_facts_v1 WHERE '+scope.rows_where(attempt))
        if int(bounds[0]['bad']):raise ValueError('Reference facts extend beyond their certified range')
        return proof

    def receipt(self,scope):
        rows=self.query(f'SELECT * FROM {self.database}.research_reference_receipt_v1 WHERE '+scope.receipt_where())
        if not rows:return None
        if len(rows)!=1:raise ValueError('Reference receipts are duplicated or ambiguous')
        row=rows[0]
        if row['parent_hash']!=scope.parent_hash or row['catalog_hash']!=scope.catalog_hash:
            raise ValueError('Reference source/catalog dependencies changed')
        proof=self.evidence(scope,row['attempt_id'])
        if (int(row['status']) not in (1,2) or int(row['output_rows'])!=proof['n']
                or row['output_hash']!=digest(proof) or (int(row['status'])==2)!=(proof['n']==0)):
            raise ValueError('Reference receipt does not match its actual output')
        self.claim.check();return row

    def publish(self,scope,attempt,expected):
        self.claim.check()
        old=self.receipt(scope)
        if old:
            if old['attempt_id']!=attempt:raise ValueError('Another reference attempt is published')
            return old
        actual=self.evidence(scope,attempt)
        if actual!=expected:raise ValueError('Reference output differs from independently derived evidence')
        row=dict(dataset_id=scope.dataset_id,entity_kind=scope.entity_kind,entity_key=scope.entity_key,
                 family=scope.family,range_start=str(scope.start),range_end=str(scope.end),attempt_id=attempt,
                 status=1 if actual['n'] else 2,output_rows=actual['n'],output_hash=digest(actual),
                 parent_hash=scope.parent_hash,catalog_hash=scope.catalog_hash,
                 published_at=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'))
        self.claim.check();insert_rows(self.query,self.database,'research_reference_receipt_v1',[row])
        self.claim.check();return self.receipt(scope)
