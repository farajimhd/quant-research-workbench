"""Frozen app formula catalog and causal vectorized fundamental projection.

Arithmetic is the app's 19-formula catalog. Certified alignment additionally
requires known matching periods, units and namespaces. ROA/ROE keep their
explicit point-in-time approximation; incomplete debt is never called total.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import UUID

from .contracts import digest,identifier,literal
from .facts import Metric,CURRENCY_UNITS

VERSION='aligned-public-fundamentals-v1'
APP_ARITHMETIC_BASELINE='490bbde642e7e3fb259ba83b590d0a355324e498de72683e2a7bd2a88c204416'


@dataclass(frozen=True)
class Formula:
    name: str
    unit: str
    labels: tuple[str,...]
    expression: str
    kind: int=2
    mode: str='aligned'
    positive_denominator: bool=False

    @property
    def key(self):return Metric('derived.'+self.name,self.unit).key


def metric(label):
    return Metric('fundamental.'+label.lower().replace(' ','_').replace('&','and'),'USD').key


def definitions():
    return (
      Formula('free_cash_flow','USD',('Operating cash flow','Capital expenditure'),'a-abs(b)'),
      Formula('gross_margin','percent',('Gross profit','Revenue'),'100*a/nullIf(b,0)'),
      Formula('operating_margin','percent',('Operating income','Revenue'),'100*a/nullIf(b,0)'),
      Formula('net_margin','percent',('Net income','Revenue'),'100*a/nullIf(b,0)'),
      Formula('free_cash_flow_margin','percent',('Operating cash flow','Capital expenditure','Revenue'),'100*(a-abs(b))/nullIf(c,0)'),
      Formula('return_on_assets','percent',('Assets','Net income'),'100*b/nullIf(a,0)',1,'approximate'),
      Formula('return_on_equity','percent',("Stockholders' equity",'Net income'),'100*b/nullIf(a,0)',1,'approximate',True),
      Formula('working_capital','USD',('Current assets','Current liabilities'),'a-b',1),
      Formula('current_ratio','ratio',('Current assets','Current liabilities'),'a/nullIf(b,0)',1),
      Formula('debt_to_equity','ratio',("Stockholders' equity",'Total borrowings','Debt current component','Debt noncurrent component'),'debt/nullIf(a,0)',1,'debt',True),
      Formula('net_debt','USD',('Cash','Total borrowings','Debt current component','Debt noncurrent component'),'debt-a',1,'debt'),
      Formula('interest_coverage','ratio',('Operating income','Interest expense'),'a/nullIf(abs(b),0)'),
      Formula('revenue_growth','percent',('Revenue',),'100*(a-previous)/nullIf(abs(previous),0)',2,'growth'),
      Formula('earnings_growth','percent',('Net income',),'100*(a-previous)/nullIf(abs(previous),0)',2,'growth'),
      Formula('share_growth','percent',('Weighted average basic shares',),'100*(a-previous)/nullIf(abs(previous),0)',2,'growth'),
      Formula('dilution','percent',('Weighted average diluted shares','Weighted average basic shares'),'100*(a-b)/nullIf(b,0)'),
      Formula('cash_conversion','ratio',('Operating cash flow','Net income'),'a/nullIf(b,0)'),
      Formula('research_intensity','percent',('Research & development','Revenue'),'100*a/nullIf(b,0)'),
      Formula('sga_intensity','percent',('SG&A expense','Revenue'),'100*a/nullIf(b,0)'),
    )


def app_contract():
    """Freeze formulas from source without initializing backend/UI services."""
    root=Path(__file__).resolve().parents[2]
    tree=ast.parse((root/'src/backend/ticker_facts_service.py').read_text(encoding='utf-8'))
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='analyze_fundamentals')
    values=next(n.value for n in function.body if isinstance(n,ast.Assign)
                and any(isinstance(t,ast.Name) and t.id=='metrics' for t in n.targets))
    rows=[]
    for call in values.elts:
        rows.append(dict(name=ast.literal_eval(call.args[0]),unit=ast.literal_eval(call.args[3]),
            formula=ast.literal_eval(call.args[5]),arithmetic=ast.dump(call.args[2],include_attributes=False)))
    if {r['name'] for r in rows}!={f.name for f in definitions()} or len(rows)!=19:
        raise ValueError('App derived catalog changed; explicit formula alignment is required')
    prefix=[]
    for node in function.body:
        prefix.append(ast.dump(node,include_attributes=False))
        if isinstance(node,ast.Assign) and node.value is values:break
    helpers=('aligned_values','latest_total_debt','safe_ratio','positive_denominator_ratio',
        'positive_denominator_percent','percent_ratio','comparable_facts','comparable_growth','fact_observations')
    arithmetic=digest(dict(prefix=prefix,helpers=[ast.dump(n,include_attributes=False) for n in tree.body
        if isinstance(n,ast.FunctionDef) and n.name in helpers]))
    if APP_ARITHMETIC_BASELINE is not None and arithmetic!=APP_ARITHMETIC_BASELINE:
        raise ValueError('App formula arithmetic changed; certified implementation must be reviewed/versioned')
    return dict(version=VERSION,app=rows,source_arithmetic_hash=arithmetic,alignment='known identical spans/units/frameworks; annual preferred',
        approximations='ROA/ROE latest comparable income over latest balance; complete debt required')


def contract_hash():return digest(app_contract())


def projection(database,dataset_id,attempt_id,entity_key,parent_attempt,start: date,end: date,*,wide=False):
    identifier(database);UUID(dataset_id);UUID(attempt_id);UUID(parent_attempt)
    if type(entity_key) is not int or not 0<entity_key<2**64 or type(start) is not date or type(end) is not date or start>end:
        raise ValueError('Invalid derived-fundamental scope')
    formulas=definitions();spec=[];expressions=[];deps=[];positive=[]
    for f in formulas:
        keys=tuple(metric(label) for label in f.labels)
        key_array='['+','.join(f'toUInt32({k})' for k in keys)+']'
        spec.append(f'({f.key},{keys[0]},{f.kind},{literal(f.mode)},{key_array},{literal(f.name)})')
        expression=f.expression
        # Replace symbolic operands using AST rather than textual substitution.
        node=ast.parse(expression,mode='eval')
        def sql(n):
            if isinstance(n,ast.Name):
                if n.id in ('a','b','c'):
                    index=ord(n.id)-ord('a')
                    return 'income.1' if f.mode=='approximate' and index==1 else f'observations[{keys[index]}].1'
                if n.id=='debt':return f'if(mapContains(observations,{keys[1]}),observations[{keys[1]}].1,observations[{keys[2]}].1+observations[{keys[3]}].1)'
                if n.id=='previous':return 'previous_fact.1'
            if isinstance(n,ast.Constant) and type(n.value) in (int,float):return str(n.value)
            if isinstance(n,ast.BinOp) and type(n.op) in (ast.Add,ast.Sub,ast.Mult,ast.Div):
                op={ast.Add:'+',ast.Sub:'-',ast.Mult:'*',ast.Div:'/'}[type(n.op)]
                return '('+sql(n.left)+op+sql(n.right)+')'
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in ('abs','nullIf'):
                return n.func.id+'('+','.join(sql(a) for a in n.args)+')'
            raise ValueError('Unapproved derived expression node')
        expressions.extend([f'formula.1={f.key}',sql(node.body)])
        required=key_array
        if f.mode=='approximate':required=f'[toUInt32({keys[0]})]'
        if f.mode=='debt':required=f'if(mapContains(observations,{keys[1]}),[toUInt32({keys[0]}),toUInt32({keys[1]})],[toUInt32({keys[0]}),toUInt32({keys[2]}),toUInt32({keys[3]})])'
        deps.extend([f'formula.1={f.key}',required])
        if f.positive_denominator:positive.append(f'(formula.1={f.key} AND NOT observations[{keys[0]}].1>0)')
    currency_codes=','.join(str(k) for k in CURRENCY_UNITS.values())
    shares_codes=','.join(str(f.key) for f in formulas if f.name in ('share_growth','dilution'))
    income_key=metric('Net income')
    null_tuple="tuple(CAST(NULL AS Nullable(Float64)),toUInt8(0),toUInt8(0),toUInt8(0),'',toDateTime64(0,6,'UTC'),'',toUInt16(0))"
    prefix=f"""WITH source AS (
      SELECT * FROM {database}.research_facts_v1 WHERE dataset_id=toUUID({literal(dataset_id)})
        AND attempt_id=toUUID({literal(parent_attempt)}) AND entity_kind=1 AND entity_key={entity_key}
        AND basis_unit_key=unit_key AND taxonomy_key IN (1,2)
    ), cutoffs AS (SELECT available_at cutoff,max(precision) cutoff_precision FROM source
      WHERE toDate(available_at) BETWEEN toDate({literal(start)}) AND toDate({literal(end)}) GROUP BY available_at),
    latest AS (
      SELECT cutoff,cutoff_precision,metric_key,unit_key basis_unit_key,taxonomy_key,period_start,period_end,context_kind,
        argMax(tuple(value,status,precision,confidence,toString(evidence_hash),available_at,fiscal_period,fiscal_year),
          tuple(available_at,revision,evidence_hash)) observation
      FROM source CROSS JOIN cutoffs WHERE available_at<=cutoff AND period_end IS NOT NULL
      GROUP BY cutoff,cutoff_precision,metric_key,basis_unit_key,taxonomy_key,period_start,period_end,context_kind
    ), contexts AS (
      SELECT cutoff,cutoff_precision,basis_unit_key,taxonomy_key,period_start,period_end,context_kind,
        mapFromArrays(groupArray(metric_key),groupArray(observation)) observations
      FROM latest GROUP BY cutoff,cutoff_precision,basis_unit_key,taxonomy_key,period_start,period_end,context_kind
    ), specs AS (SELECT arrayJoin([{','.join(spec)}]) formula),
    candidates AS (
      SELECT *,row_number() OVER (PARTITION BY cutoff,basis_unit_key,taxonomy_key,formula.1
        ORDER BY (observations[formula.2].7='FY' AND (context_kind=0 OR
          (context_kind=2 AND dateDiff('day',period_start,period_end) BETWEEN 300 AND 400))) DESC,
          period_end DESC,dateDiff('day',period_start,period_end) DESC,period_start) ranking
      FROM contexts CROSS JOIN specs WHERE (context_kind=formula.3 OR context_kind=0)
        AND (context_kind!=2 OR period_start IS NOT NULL) AND mapContains(observations,toUInt32(formula.2))
        AND ((formula.1 IN ({shares_codes}) AND basis_unit_key=2)
             OR (formula.1 NOT IN ({shares_codes}) AND basis_unit_key IN ({currency_codes})))
    ), chosen AS (SELECT *,formula.1 formula_id FROM candidates WHERE ranking=1),
    grid AS (SELECT DISTINCT cutoff,cutoff_precision,basis_unit_key,taxonomy_key,formula,formula.1 formula_id
      FROM latest CROSS JOIN specs WHERE (formula.1 IN ({shares_codes}) AND basis_unit_key=2)
        OR (formula.1 NOT IN ({shares_codes}) AND basis_unit_key IN ({currency_codes}))),
    selected AS (
      SELECT g.cutoff cutoff,g.cutoff_precision cutoff_precision,g.basis_unit_key basis_unit_key,g.taxonomy_key taxonomy_key,g.formula formula,g.formula_id formula_id,
        r.period_start period_start,r.period_end period_end,r.context_kind context_kind,r.observations observations,r.ranking ranking
      FROM grid g LEFT JOIN chosen r USING (cutoff,basis_unit_key,taxonomy_key,formula_id)
    ),
    income_rows AS (
      SELECT *,row_number() OVER (PARTITION BY cutoff,basis_unit_key,taxonomy_key
        ORDER BY (observations[{income_key}].7='FY' AND (context_kind=0 OR
          dateDiff('day',period_start,period_end) BETWEEN 300 AND 400)) DESC,
          period_end DESC,dateDiff('day',period_start,period_end) DESC) income_rank
      FROM contexts WHERE (context_kind=0 OR (context_kind=2 AND period_start IS NOT NULL)) AND mapContains(observations,{income_key})
    ), incomes AS (SELECT cutoff,basis_unit_key,taxonomy_key,observations[{income_key}] income,
        period_start income_start,period_end income_end,context_kind income_context FROM income_rows WHERE income_rank=1),
    previous_rows AS (
      SELECT r.cutoff cutoff,r.basis_unit_key basis_unit_key,r.taxonomy_key taxonomy_key,r.formula.1 formula_id,
        argMax(tuple(p.observations[toUInt32(r.formula.2)],p.period_start,p.period_end),p.period_end) prior
      FROM chosen r INNER JOIN contexts p ON r.cutoff=p.cutoff AND r.basis_unit_key=p.basis_unit_key AND r.taxonomy_key=p.taxonomy_key
      WHERE r.formula.4='growth' AND p.context_kind=2 AND p.period_start IS NOT NULL AND p.period_end<r.period_end
        AND r.observations[toUInt32(r.formula.2)].7 IN ('FY','Q1','Q2','Q3','Q4','H1','H2')
        AND mapContains(p.observations,toUInt32(r.formula.2))
        AND p.observations[toUInt32(r.formula.2)].7=r.observations[toUInt32(r.formula.2)].7
        AND abs(dateDiff('day',p.period_start,p.period_end)-dateDiff('day',r.period_start,r.period_end))<=7
      GROUP BY cutoff,basis_unit_key,taxonomy_key,formula_id
    ), evaluated AS (
      SELECT r.cutoff cutoff,r.cutoff_precision cutoff_precision,r.basis_unit_key basis_unit_key,r.taxonomy_key taxonomy_key,r.period_start period_start,
        r.period_end period_end,r.context_kind context_kind,r.observations observations,r.formula formula,
        ifNull(i.income,{null_tuple}) income,i.income_start income_start,i.income_end income_end,i.income_context income_context,
        ifNull(p.prior.1,{null_tuple}) previous_fact,p.prior.2 previous_start,p.prior.3 previous_end,
        multiIf({','.join(deps)},CAST([] AS Array(UInt32))) dependencies,
        arrayAll(k -> mapContains(observations,k) AND observations[k].2=1 AND observations[k].1 IS NOT NULL,dependencies) complete,
        multiIf({','.join(expressions)},CAST(NULL AS Nullable(Float64))) calculated,
        ({' OR '.join(positive) or '0'}) bad_denominator,
        multiIf(r.ranking=0,'missing_primary_input',context_kind!=formula.3,'unknown_primary_context',
          NOT complete,'missing_or_invalid_aligned_inputs',bad_denominator,'nonpositive_denominator',
          formula.4='approximate' AND income_context!=2,'missing_comparable_income_context',
          formula.4='approximate' AND (income.2!=1 OR income.1 IS NULL),'missing_comparable_income',
          formula.4='growth' AND (previous_fact.2!=1 OR previous_fact.1 IS NULL),'missing_comparable_prior_period',
          calculated IS NULL,'zero_or_missing_denominator',NOT isFinite(calculated),'nonfinite_calculation','') failure,
        arrayMap(k -> tuple(k,observations[k]),dependencies) component_evidence,
        toJSONString(tuple({literal(VERSION)},{literal(contract_hash())},formula.1,basis_unit_key,taxonomy_key,
          period_start,period_end,context_kind,component_evidence,
          if(formula.4='approximate',tuple(income,income_start,income_end,income_context),tuple({null_tuple},CAST(NULL AS Nullable(Date)),CAST(NULL AS Nullable(Date)),toUInt8(0))),
          if(formula.4='growth',tuple(previous_fact,previous_start,previous_end),tuple({null_tuple},CAST(NULL AS Nullable(Date)),CAST(NULL AS Nullable(Date)))),failure)) proof,
        lower(hex(SHA256(proof))) proof_hash
      FROM selected r LEFT JOIN incomes i USING (cutoff,basis_unit_key,taxonomy_key)
        LEFT JOIN previous_rows p USING (cutoff,basis_unit_key,taxonomy_key,formula_id)
    ), changes AS (
      SELECT *,lagInFrame(proof_hash,1,'') OVER (PARTITION BY formula.1,basis_unit_key,taxonomy_key ORDER BY cutoff
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) previous_hash FROM evaluated
    ) """
    extras=",'derived_fundamentals' source_system,concat('formula/',toString(formula.1)) source_record,"+literal(contract_hash())+" source_hash,proof" if wide else ''
    return prefix+f"""SELECT toUUID({literal(dataset_id)}) dataset_id,toUUID({literal(attempt_id)}) attempt_id,
      toUInt8(1) entity_kind,toUInt64({entity_key}) entity_key,toUInt32(formula.1) metric_key,
      period_start,period_end,context_kind,observations[toUInt32(formula.2)].8 fiscal_year,
      observations[toUInt32(formula.2)].7 fiscal_period,taxonomy_key,cutoff available_at,toUInt32(0) revision,
      if(empty(failure),calculated,CAST(NULL AS Nullable(Float64))) value,
      toUInt16(if(formula.6 IN ('free_cash_flow','working_capital','net_debt'),basis_unit_key,
        if(formula.6 IN ('current_ratio','debt_to_equity','interest_coverage','cash_conversion'),3,6))) unit_key,
      basis_unit_key,toUInt8(if(empty(failure),1,2)) status,
      toUInt8(greatest(cutoff_precision,arrayMax(arrayMap(k -> observations[k].3,dependencies)),
        if(formula.4='approximate',income.3,if(formula.4='growth',previous_fact.3,0)))) precision,
      toUInt8(least(arrayMin(arrayMap(k -> observations[k].4,dependencies)),
        if(formula.4='approximate',least(income.4,60),if(formula.4='growth',previous_fact.4,90)))) confidence,
      if(notEmpty(failure),failure,if(formula.4='approximate','point_in_time_approximation','')) reason,
      proof_hash evidence_hash{extras} FROM changes WHERE proof_hash!=previous_hash"""
