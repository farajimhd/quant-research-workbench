"""SELECT-only committed canonical archive metadata; no installed strategy authority."""
from dataclasses import dataclass,field
from datetime import date,datetime,time,timezone
import json
from types import MappingProxyType
from zoneinfo import ZoneInfo

from pipelines.market_sip.events.trade_reporting_flags import REVISION
from src.market_engine.derived_trade_policy import POLICY
from src.market_engine.canonical_v7_archive_contract import (
    MEMBER_TABLE,COMMIT_TABLE,MEMBER_COLUMNS,COMMIT_COLUMNS,MAX_MEMBERS,
    require_hash,validate_commit,validate_member,scope_hash,
)
from .backtest_market_data import assert_select_only
from .backtest_declared_ladder_seed import _previous_session
from .structural_v7_seed import _assemble_seed,_validate_coverage,_LEVEL_COLUMNS,_OBSERVATION_COLUMNS


def _literal(value):
    return "'"+value.replace('\\','\\\\').replace("'","\\'")+"'"


def _rows(client,query):
    return [json.loads(line) for line in client.execute(assert_select_only(query)).splitlines() if line.strip()]


def _fence(day):
    cutoff=datetime.combine(day,time(4),ZoneInfo('America/New_York')).astimezone(timezone.utc)
    return cutoff.strftime('%Y-%m-%d %H:%M:%S')+'.000000000'


@dataclass(frozen=True,slots=True)
class CanonicalV7Inventory:
    """Immutable wire inventory; collection identity never replaces successor identity."""
    commit_json: str
    members_json: str
    _index: object=field(init=False,repr=False,compare=False)
    _token: str=field(init=False,repr=False,compare=False)

    def __post_init__(self):
        if type(self.commit_json) is not str or type(self.members_json) is not str:
            raise ValueError('Canonical inventory requires immutable JSON wire values')
        commit=json.loads(self.commit_json)
        rows=tuple(json.loads(self.members_json))
        validate_commit(commit,rows)
        index={}
        for row in rows:
            index.setdefault(row['target_session'],{})[row['ticker']]=MappingProxyType(row)
        object.__setattr__(self,'_index',MappingProxyType({day:MappingProxyType(values) for day,values in index.items()}))
        object.__setattr__(self,'_token',commit['content_hash'])

    @property
    def token(self):
        return self._token

    def for_scope(self,*,target_session,tickers,scope_hash):
        """Select only certified dated requests; never infer membership for a gap day."""
        require_hash(scope_hash)
        if (type(target_session) is not str
                or date.fromisoformat(target_session).isoformat()!=target_session
                or type(tickers) is not tuple or not tickers
                or any(type(t) is not str or not t for t in tickers)
                or tickers!=tuple(sorted(set(tickers)))):
            raise ValueError('Canonical seed scope requires exact ordered dated tickers')
        day=date.fromisoformat(target_session)
        prior=_previous_session(day).isoformat()
        fence=_fence(day)
        requested=set(tickers)
        dated=self._index.get(target_session,{})
        selected={ticker:dated[ticker] for ticker in tickers if ticker in dated}
        if set(selected)!=requested:
            raise ValueError('Canonical archive lacks committed exact dated membership')
        for row in selected.values():
            if (row['scope_hash']!=scope_hash or row['seed_session']!=prior
                    or row['available_at']>fence or row['input_policy']!=POLICY
                    or row['reporting_revision']!=REVISION):
                raise ValueError('Canonical seed membership policy, prior session, scope or availability differs')
        return tuple(dict(selected[ticker]) for ticker in tickers)


def certify_inventory(client,*,consolidation_hash):
    """Recompute the complete committed inventory with explicitly bounded SELECTs."""
    require_hash(consolidation_hash)
    if client.execute("SELECT getSetting('readonly')").strip()!='1':
        raise ValueError('Canonical archive reader requires a SELECT-only principal')
    where='consolidation_hash='+_literal(consolidation_hash)
    commits=_rows(client,'SELECT '+','.join(name for name,_ in COMMIT_COLUMNS)
                  +' FROM arte.'+COMMIT_TABLE+' FINAL WHERE '+where+' LIMIT 2 FORMAT JSONEachRow')
    if len(commits)!=1:
        raise ValueError('Canonical archive consolidation is absent or duplicated')
    commit=commits[0]
    count=commit.get('member_count')
    if type(count) is not int or not 0<count<=MAX_MEMBERS:
        raise ValueError('Canonical archive inventory count exceeds its transport bound')
    rows=tuple(_rows(client,'SELECT '+','.join(name for name,_ in MEMBER_COLUMNS)
                     +' FROM arte.'+MEMBER_TABLE+' FINAL WHERE '+where
                     +' ORDER BY ticker,target_session LIMIT '+str(count+1)+' FORMAT JSONEachRow'))
    validate_commit(commit,rows)
    if commit['consolidation_hash']!=consolidation_hash:
        raise ValueError('Canonical archive returned a foreign consolidation')
    encode=lambda value:json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
    return CanonicalV7Inventory(encode(commit),encode(rows))


def decode_member_seed(member,*,coverage,levels,observations):
    """Decode content for an already committed member; preserve raw and projected hashes."""
    validate_member(member)
    day=date.fromisoformat(member['target_session'])
    ticker=member['ticker']
    links={'ticker':ticker,'session_date':member['seed_session'],
           'available_at':member['available_at'],'source_plan_hash':member['successor_plan_hash'],
           'source_checkpoint_hash':member['checkpoint_hash'],'input_policy':member['input_policy'],
           'reporting_revision':member['reporting_revision']}
    if any(coverage.get(key)!=value for key,value in links.items()):
        raise ValueError('Canonical coverage differs from committed member identities')
    if (member['seed_session']!=_previous_session(day).isoformat()
            or member['input_policy']!=POLICY or member['reporting_revision']!=REVISION
            or member['available_at']>_fence(day)):
        raise ValueError('Canonical seed decoder lacks exact supported prior authority')
    _validate_coverage(coverage,ticker=ticker,session=day)
    seed=_assemble_seed(ticker,day,coverage,levels,observations)
    if seed['checkpoint_hash']!=member['seed_content_hash']:
        raise ValueError('Canonical reconstructed seed content differs from committed projected hash')
    return seed


def load_seed_batch(client,inventory,*,target_session,tickers,source_scope):
    """Read at most eight seeds from V2; upstream market/price proofs remain separate gates."""
    if (type(inventory) is not CanonicalV7Inventory or type(tickers) is not tuple
            or not 1<=len(tickers)<=8 or type(source_scope) is not dict
            or source_scope.get('target_session')!=target_session):
        raise ValueError('Canonical seed batch requires a committed inventory and bounded exact scope')
    if client.execute("SELECT getSetting('readonly')").strip()!='1':
        raise ValueError('Canonical seed batch requires a SELECT-only principal')
    members=inventory.for_scope(target_session=target_session,tickers=tickers,scope_hash=scope_hash(source_scope))
    day=date.fromisoformat(target_session)
    names=','.join(_literal(ticker) for ticker in tickers)
    prior=_previous_session(day).isoformat()
    covers=_rows(client,'SELECT * FROM arte.structural_level_coverage_v7_v2 FINAL WHERE ticker IN ('
                 +names+') AND session_date=toDate('+_literal(prior)+') ORDER BY ticker LIMIT '
                 +str(len(tickers)+1)+' FORMAT JSONEachRow')
    coverage={row['ticker']:row for row in covers}
    if len(covers)!=len(tickers) or set(coverage)!=set(tickers):
        raise ValueError('Canonical V2 coverage is missing, duplicated or foreign')
    counts={}
    for name in ('level_count','observation_count'):
        total=0
        for row in covers:
            value=row.get(name)
            if type(value) is str and value.isascii() and value.isdecimal() and str(int(value))==value:
                value=int(value)
            if type(value) is not int or value<0:
                raise ValueError('Canonical V2 coverage count type/value differs')
            total+=value
        if total>1_000_000:
            raise ValueError('Canonical seed batch exceeds explicit one-million-row transport bound')
        counts[name]=total
    fence=_literal(_fence(day))
    predicate='ticker IN ('+names+') AND valid_from<=toDateTime64('+fence+",9,'UTC') AND (isNull(valid_to) OR valid_to>toDateTime64("+fence+",9,'UTC'))"
    grouped={ticker:{'levels':[],'observations':[]} for ticker in tickers}
    for table,columns,kind,count_key,order in (
            ('structural_levels_v7_v2',_LEVEL_COLUMNS,'levels','level_count','ticker,level_id'),
            ('structural_level_observations_v7_v2',_OBSERVATION_COLUMNS,'observations','observation_count','ticker,level_id,observation_id')):
        rows=_rows(client,'SELECT '+columns+' FROM arte.'+table+' FINAL WHERE '+predicate
                   +' ORDER BY '+order+' LIMIT '+str(counts[count_key]+1)+' FORMAT JSONEachRow')
        if len(rows)!=counts[count_key] or any(row.get('ticker') not in grouped for row in rows):
            raise ValueError('Canonical seed rows differ from complete certified counts/scope')
        for row in rows:grouped[row['ticker']][kind].append(row)
    return {member['ticker']:decode_member_seed(member,coverage=coverage[member['ticker']],
            levels=grouped[member['ticker']]['levels'],observations=grouped[member['ticker']]['observations']) for member in members}
