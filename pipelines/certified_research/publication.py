"""Attempt-scoped ClickHouse readback and restart-safe publication receipts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date,datetime,timezone
from hashlib import sha256
import json
import re
from uuid import UUID,uuid4,uuid5,NAMESPACE_URL

from .contracts import digest,identifier,literal
from .schema import TABLES,verify_storage

PRODUCTS={'bars':'research_bars_v1','technical':'research_indicators_v1',
          'broker_100ms':'research_liquidity_v1','v7':'research_v7_checkpoint_v1'}
CERTIFIED=1
COVERED_EMPTY=2


@dataclass(frozen=True)
class Scope:
    dataset_id: str
    session_date: date
    listing_key: int
    product: str
    parent_hash: str

    def __post_init__(self):
        UUID(self.dataset_id)
        if (type(self.session_date) is not date or type(self.listing_key) is not int
                or not 0<self.listing_key<2**64 or self.product not in PRODUCTS
                or not re.fullmatch(r'[0-9a-f]{64}',self.parent_hash)):
            raise ValueError('Invalid publication scope')

    def where(self,attempt=None):
        value=(f'dataset_id=toUUID({literal(self.dataset_id)}) AND '
               f'session_date=toDate({literal(self.session_date)}) AND listing_key={self.listing_key}')
        if attempt is not None:
            UUID(attempt);value+=f' AND attempt_id=toUUID({literal(attempt)})'
        return value


class KeeperClaim:
    """One ephemeral campaign writer; expired/replaced sessions cannot publish."""
    def __init__(self,client,dataset_id,*,owner=None):
        UUID(dataset_id)
        if not client.connected or client.client_id is None or getattr(client,'client_state',None)=='CONNECTED_RO':
            raise RuntimeError('Research Keeper is unavailable')
        self.client=client;self.session=client.client_id;self.owner=owner or str(uuid4())
        self.invalidated=False
        self.listener=lambda state:self._state(state)
        if hasattr(client,'add_listener'):client.add_listener(self.listener)
        self.lock=client.Lock('/research/certified-data/v1/claims/'+dataset_id,identifier=self.owner)
        if not self.lock.acquire(blocking=False):raise RuntimeError('Another writer owns this dataset')

    def _state(self,state):
        if getattr(state,'name',str(state))!='CONNECTED':self.invalidated=True

    def check(self):
        if (self.invalidated or not self.client.connected or self.client.client_id!=self.session
                or not self.lock.is_acquired or getattr(self.client,'client_state',None)=='CONNECTED_RO'):
            raise RuntimeError('Research writer ownership is uncertain or expired')
        contenders=self.lock.contenders()
        if not contenders or contenders[0]!=self.owner:
            raise RuntimeError('Research writer ownership is uncertain or expired')

    def close(self):
        if not self.invalidated and self.client.connected and self.client.client_id==self.session and self.lock.is_acquired:
            self.lock.release()
        if hasattr(self.client,'remove_listener'):self.client.remove_listener(self.listener)


class DictionaryClaim(KeeperClaim):
    """Serialize shared identity/provenance writes across dataset writers."""
    def __init__(self,client,database,*,owner=None):
        self.dictionary_database=identifier(database)
        resource=str(uuid5(NAMESPACE_URL,'certified-research-dictionaries-v1:'+database))
        super().__init__(client,resource,owner=owner)


def insert_rows(query,database,table,rows,*,maximum_rows=4096,maximum_bytes=8*1024**2):
    """Bounded JSONEachRow transport; source precision and no automatic retry."""
    identifier(database);identifier(table)
    if not rows:return
    if len(rows)>maximum_rows:raise ValueError('Insert batch exceeds row bound')
    columns=tuple(rows[0])
    if any(set(row)!=set(columns) for row in rows):raise ValueError('Insert row columns differ')
    for col in columns:identifier(col)
    body='\n'.join(json.dumps(row,separators=(',',':'),allow_nan=False,default=str) for row in rows)
    if len(body.encode())>maximum_bytes:raise ValueError('Insert batch exceeds byte bound')
    query(f"INSERT INTO {database}.{table} ({','.join(columns)}) FORMAT JSONEachRow\n{body}",read=False)


def evidence_sql(table,source,key):
    columns=[c.name for c in table.columns if not (table.name=='research_v7_checkpoint_v1' and c.name=='payload')]
    expression='SHA256(toString(tuple('+','.join(columns)+')))'
    sums=','.join(f'sum(reinterpretAsUInt64(substring(row_hash,{offset},8))) h{i}'
                  for i,offset in enumerate((1,9,17,25)))
    return (f'SELECT count() n,uniqExact({key}) unique_keys,{sums} FROM '
            f'(SELECT *,{expression} row_hash FROM ({source}))')


class Publisher:
    def __init__(self,query,database,claim):
        self.query=query;self.database=identifier(database);self.claim=claim
        self.claim.check()
        verify_storage(query,database,require_tables=True)

    def evidence(self,scope: Scope,attempt: str):
        table=next(t for t in TABLES if t.name==PRODUCTS[scope.product])
        # Four independent UInt64 lanes from each SHA256, bounded aggregate
        # state. This is a multiset fingerprint plus exact key/count checks,
        # not a Merkle proof or an assertion of adversarial collision immunity.
        key='tuple(boundary)' if scope.product=='v7' else 'tuple(resolution_ms,bucket_index)'
        if scope.product=='v7':
            valid=self.query(f'SELECT countIf(lower(hex(SHA256(payload)))!=state_hash) bad '
                             f'FROM {self.database}.{table.name} WHERE {scope.where(attempt)}')
            if len(valid)!=1 or int(valid[0]['bad']):raise ValueError('Stored checkpoint payload hash mismatch')
        rows=self.query(evidence_sql(table,f'SELECT * FROM {self.database}.{table.name} '
                                   f'WHERE {scope.where(attempt)}',key))
        if len(rows)!=1:raise ValueError('Output evidence is absent or ambiguous')
        result={k:int(v) for k,v in rows[0].items()}
        if result['n']!=result['unique_keys']:raise ValueError('Duplicate output keys')
        return result

    def receipt(self,scope: Scope):
        rows=self.query(f'SELECT * FROM {self.database}.research_unit_receipt_v1 WHERE '
                        f'{scope.where()} AND product={literal(scope.product)}')
        if not rows:return None
        if len(rows)!=1:raise ValueError('Ambiguous publication receipts; explicit reconciliation required')
        row=rows[0]
        if row['parent_hash']!=scope.parent_hash or int(row['status']) not in (CERTIFIED,COVERED_EMPTY):
            raise ValueError('Receipt does not match current dependencies')
        proof=self.evidence(scope,row['attempt_id'])
        if (int(row['output_rows'])!=proof['n'] or row['output_hash']!=digest(proof)
                or (int(row['status'])==COVERED_EMPTY)!=(proof['n']==0)):
            raise ValueError('Published output changed or its empty status is invalid')
        self.claim.check()
        return row

    def publish(self,scope: Scope,attempt: str,*,expected=None,empty_proof=None):
        self.claim.check()
        existing=self.receipt(scope)
        if existing is not None:
            if existing['attempt_id']!=attempt:raise ValueError('Different attempt is already published')
            return existing
        proof=self.evidence(scope,attempt)
        if expected is None:raise ValueError('Publication requires independently derived expected evidence')
        if proof!=expected:raise ValueError('Output readback differs from expected evidence')
        if proof['n']==0 and not (isinstance(empty_proof,str) and re.fullmatch(r'[0-9a-f]{64}',empty_proof)):
            raise ValueError('Empty output needs verified source/derivation proof')
        self.claim.check()
        row=dict(dataset_id=scope.dataset_id,session_date=str(scope.session_date),listing_key=scope.listing_key,
                 attempt_id=attempt,product=scope.product,status=CERTIFIED if proof['n'] else COVERED_EMPTY,
                 output_rows=proof['n'],output_hash=digest(proof),parent_hash=scope.parent_hash,
                 reason='' if proof['n'] else empty_proof,
                 published_at=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'))
        # On uncertain insert, the next resume reads/reconciles the same receipt;
        # it never blindly sends the insert again.
        insert_rows(self.query,self.database,'research_unit_receipt_v1',[row])
        self.claim.check()
        return self.receipt(scope)
