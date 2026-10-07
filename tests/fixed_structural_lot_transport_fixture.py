"""Controlled bounded SELECT transport; no installed or database authority."""
from tests.test_arte_journal_writer import MemoryClient

class ExactDecisionTransport(MemoryClient):
    def execute(self,sql,*,query_id=None):
        if sql=='SELECT currentUser()':
            return 'backtest_v4_fixed_structural_lot_runner'
        if type(sql) is str and sql.startswith('SELECT '):
            import json,re
            found=re.search(r'FROM arte\.([a-z0-9_]+)',sql)
            name=found.group(1) if found else ''
            if ('snapshot' in name or name.startswith('trading_fixed_structural_lot_protection')
                    or name.startswith('trading_strategy_one_manager_')
                    or name.startswith('trading_strategy_one_protection_')
                    or name.startswith('trading_strategy_one_evidence_')
                    or name.startswith('trading_strategy_one_campaign_')
                    or name in {v[0] for v in __import__('src.trading_runtime.arte_portfolio_snapshot',fromlist=['_SNAPSHOT_FAMILIES'])._SNAPSHOT_FAMILIES}
                    or name=='trading_portfolio_snapshot_commit_v1'
                    or name.startswith('trading_strategy_one_broker_match_')
                    or name.startswith('trading_strategy_one_oms_observation_')):
                from src.trading_runtime.arte_journal_writer import _CONTRACTS
                values=list(self.tables.get(name,()))
                for field in ('run_id','account_id'):
                    match=re.search(rf"(?:WHERE|AND) {field}='([^']+)'",sql)
                    if match:values=[v for v in values if v[field]==match.group(1)]
                for field in ('state_revision','checkpoint_sequence','through_sequence'):
                    match=re.search(rf'(?:WHERE|AND) {field}=(\d+)',sql)
                    if match:values=[v for v in values if int(v[field])==int(match.group(1))]
                if 'snapshot_id=' in sql or 'snapshot_id IN (' in sql:
                    ids=set(re.findall(r"toUUID\('([0-9a-f-]+)'\)",sql))
                    values=[v for v in values if v['snapshot_id'] in ids]
                if 'ORDER BY state_revision DESC' in sql:
                    values.sort(key=lambda v:v['state_revision'],reverse=True)
                match=re.search(r'LIMIT (\d+)',sql)
                if match:values=values[:int(match.group(1))]
                text=sql.split('SELECT ',1)[1].split(' FROM ',1)[0]
                columns=([column for column,_ in _CONTRACTS[name].columns] if text=='*' else
                    [v.rsplit(' AS ',1)[-1] for v in text.split(',')])
                return '\n'.join(json.dumps({k:v[k] for k in columns}) for v in values)
        if type(sql) is str and sql.startswith('SELECT ') and 'AND root_record_id=toUUID(' in sql:
            import json,re
            identity=re.search(r"AND root_record_id=toUUID\('([^']+)'\)",sql).group(1)
            limit=re.search(r'LIMIT (\d+)',sql)
            body=super().execute(re.sub(r'LIMIT \d+','',sql))
            values=[json.loads(v) for v in body.splitlines() if v.strip()]
            values=[v for v in values if v['root_record_id']==identity]
            if limit:values=values[:int(limit.group(1))]
            return '\n'.join(json.dumps(v) for v in values)
        if type(sql) is str and sql.startswith('SELECT c.*,e.sequence AS event_sequence'):
            import json,re
            wanted_run=re.search(r"WHERE c.run_id='([^']+)'",sql).group(1)
            through=int(re.search(r'e.sequence<=(\d+)',sql).group(1))
            batches={row['batch_id'] for row in self.tables.get('trading_commit_v4',())
                if row['run_id']==wanted_run and row['last_sequence']<=through}
            joined=[]
            for cursor in self.tables.get('trading_backtest_cursor_v1',()):
                for event in self.tables.get('trading_event_v1',()):
                    if (cursor['run_id']==wanted_run and cursor['batch_id'] in batches
                            and all(cursor[key]==event[key] for key in ('run_id','batch_id','record_id'))
                            and event['sequence']<=through):
                        joined.append({**cursor,'event_sequence':event['sequence'],
                            'event_category':event['category'],'event_entity_type':event['entity_type'],
                            'event_entity_id':event['entity_id']})
            joined.sort(key=lambda row:row['event_sequence'],reverse=True)
            return '\n'.join(json.dumps(row) for row in joined[:2])
        if type(sql) is str and sql.startswith('SELECT') and 'AND sequence IN (' in sql:
            import json,re
            wanted=set(map(int,sql.split('AND sequence IN (',1)[1].split(')',1)[0].split(',')))
            # The controlled memory transport lacks this real SQL
            # predicate. Apply it before LIMIT, preserving row order.
            limit=re.search(r'LIMIT (\d+)',sql)
            body=super().execute(re.sub(r'LIMIT \d+','',sql))
            rows=[json.loads(line) for line in body.splitlines() if line]
            rows=[row for row in rows if int(row['sequence']) in wanted]
            if limit:rows=rows[:int(limit.group(1))]
            return '\n'.join(json.dumps(row) for row in rows)
        if type(sql) is str and sql.startswith('SELECT') and 'AND decision_id IN (' in sql:
            import json,re
            wanted=set(re.findall(r"'([^']+)'",sql.split('AND decision_id IN (',1)[1].split(')',1)[0]))
            limit=re.search(r'LIMIT (\d+)',sql)
            body=super().execute(re.sub(r'LIMIT \d+','',sql))
            rows=[row for line in body.splitlines() if line
                for row in (json.loads(line),) if row['decision_id'] in wanted]
            if limit:rows=rows[:int(limit.group(1))]
            return '\n'.join(json.dumps(row) for row in rows)
        return super().execute(sql)
