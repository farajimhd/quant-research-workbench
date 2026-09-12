"""Execute the shared TypeScript entry projection using the frontend runtime."""
import json
import shutil
import subprocess
from pathlib import Path

from src.runtime_paths import frontend_runtime_root


def test_entry_hod_survives_activity_and_chart_compaction():
    from src.backend.trading_runtime_service import _compact_strategy_gate_snapshot
    from src.backend.replay_run_service import _compact_strategy_chart_plan
    selection = {"prior_snapshot_session_high": 3.62,
                 "prior_snapshot_selected_at": "2026-08-21T08:10:22Z",
                 "prior_snapshot_levels": [{"price": 3.6}, {"price": 3.55}, {"price": 3.52}]}
    result = _compact_strategy_chart_plan(_compact_strategy_gate_snapshot({"unified_structural_trigger": selection}))
    assert result["unified_structural_trigger"] == selection


def test_waiting_reference_changes_survive_runtime_and_chart_projection(tmp_path):
    from types import SimpleNamespace
    from datetime import datetime,timezone
    from src.trading_runtime.runtime import TradingRuntime
    from src.trading_runtime.journal import TradingJournal
    from src.trading_runtime.signals import StrategySignal,StrategyEvaluation
    from src.backend.trading_runtime_service import strategy_activity_payload
    from src.backend.replay_run_service import _compact_strategy_chart_plan
    journal=TradingJournal(tmp_path/'reference.sqlite3')
    runtime=TradingRuntime.__new__(TradingRuntime)
    runtime.run_id='r';runtime.config=SimpleNamespace(strategy_id='s',strategy_revision=47)
    runtime.journal=journal;runtime._last_wait_decision_signatures={}
    try:
        for i,changed in enumerate((True,False,True,False)):
            reference=dict(at=100+i,hod=10.5,resistance_upper=10.2+i//2,changed=changed)
            signal=StrategySignal(signal_id=str(i),signal_type='waiting',ticker='TEST',
                event_time=datetime.fromtimestamp(100+i,timezone.utc),action='wait',direction='neutral',
                score=0.,confidence=0.,reason='macd_closed',metadata={'historical_hod_reference':reference})
            runtime._record_strategy_signals(StrategyEvaluation(signals=(signal,)),'a')
        rows=journal.strategy_activity_records(run_id='r',consequential_only=True,compact=True)
        assert len(rows)==2
        projected=strategy_activity_payload(journal=journal,run_id='r',consequential_only=True,include_decision_evidence=False)['rows']
        refs=[_compact_strategy_chart_plan(row['gate_snapshot'])['historical_hod_reference'] for row in projected]
        assert {ref['at'] for ref in refs}=={100,102}
    finally:journal.close()


def test_entry_selection_is_causal_and_separate_from_targets():
    runtime = frontend_runtime_root()
    source = Path(__file__).resolve().parents[1] / "frontend/src/features/canvas/entryStructurePresentation.ts"
    script = r'''
const {createRequire} = await import('node:module');
const require = createRequire(process.cwd() + '/package.json');
const ts = require('typescript');
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(SOURCE_PATH, 'utf8');
const js = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {entryStructurePresentation: project} = await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'));
const t = Date.parse('2026-08-21T08:10:23Z') / 1000;
const snapshot = {selected_at:'2026-08-21T08:10:23Z',session_high:3.62,
  levels:[{price:3.6},{price:3.55},{price:3.52}]};
assert.deepEqual(project({current_snapshot:snapshot, profit_target_selection:{qualified_levels:[{price:4},{price:5}]}},t),
  {highOfDayPrice:3.62,resistancePrices:[3.6,3.55,3.52]});
assert.deepEqual(project({current_snapshot:{...snapshot,levels:[{price:4},{price:3.55},{price:3.55},{price:-1}]}},t),
  {highOfDayPrice:3.62,resistancePrices:[3.55]});
assert.deepEqual(project({current_snapshot:{...snapshot,levels:[]},prior_snapshot_levels:[{price:3.5}]},t).resistancePrices,[]);
assert.deepEqual(project({profit_target_selection:{qualified_levels:[{price:3.5}]}},t).resistancePrices,[]);
assert.deepEqual(project({prior_snapshot_session_high:3.62,prior_snapshot_selected_at:snapshot.selected_at,
  prior_snapshot_levels:snapshot.levels},t).resistancePrices,[3.6,3.55,3.52]);
assert.deepEqual(project({current_snapshot:{...snapshot,selected_at:'2026-08-21T08:10:24Z'}},t).resistancePrices,[]);
assert.deepEqual(project({current_snapshot:{...snapshot,session_high:4,levels:[{price:3.7},{price:3.8},{price:3.9}]}},t).resistancePrices,[3.9,3.8,3.7]);
console.log('7 entry-selection scenarios passed');
'''.replace("SOURCE_PATH", json.dumps(str(source)))
    result = subprocess.run([shutil.which("node") or "node", "--input-type=module", "-"],
                            input=script, text=True, cwd=runtime, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_continuous_references_preserve_strategy_values_and_causal_cutoff():
    source=Path(__file__).resolve().parents[1]/'frontend/src/features/canvas/strategyReferencePresentation.ts'
    script=r'''
const {createRequire}=await import('node:module');
const require=createRequire(process.cwd()+'/package.json');
const ts=require('typescript'),assert=require('node:assert/strict');
const js=ts.transpileModule(require('node:fs').readFileSync(SOURCE_PATH,'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {strategyReferencePresentation:project}=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));
const row=(at,hod,r,ticker='SUGP')=>({ticker,event_time:new Date(at*1000).toISOString(),chart_plan:{historical_hod_reference:{at,hod,resistance_upper:r,changed:true}}});
const rows=[row(10,4.5,4.44),row(11,4.5,4.44),row(12,4.6,4.55),row(14,null,null),row(16,99,98),row(13,100,90,'JUNS')];
assert.deepEqual(project(rows,'SUGP',new Date(15000).toISOString()),[
 {start:10,end:12,hod:4.5,resistance:4.44},{start:12,end:14,hod:4.6,resistance:4.55},
 {start:14,end:15,hod:undefined,resistance:undefined}]);
assert.deepEqual(project(rows,'SUGP',new Date(9000).toISOString()),[]);
assert.equal(project(rows,'SUGP',new Date(11500).toISOString())[0].end,11.5);
'''.replace('SOURCE_PATH',json.dumps(str(source)))
    result=subprocess.run([shutil.which('node') or 'node','--input-type=module','-'],input=script,text=True,
        cwd=frontend_runtime_root(),capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr


def test_entry_reference_guide_never_extends_before_decision():
    runtime = frontend_runtime_root()
    source = Path(__file__).resolve().parents[1] / "frontend/src/app/components/tradeGuideGeometry.ts"
    script = r'''
const {createRequire} = await import('node:module');
const require = createRequire(process.cwd() + '/package.json');
const ts = require('typescript');
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(SOURCE_PATH, 'utf8');
const js = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {tradeGuideSpan} = await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'));
// A subsecond entry/exit must not smear its later HOD/R1-R3 into earlier bars.
assert.deepEqual(tradeGuideSpan(200,200.2,1000,true),{left:200,right:200.2});
assert.deepEqual(tradeGuideSpan(200,200,1000,true),{left:200,right:200});
assert.deepEqual(tradeGuideSpan(-10,20,1000,true),{left:0,right:20});
assert.deepEqual(tradeGuideSpan(990,1020,1000,true),{left:990,right:1000});
assert.ok(tradeGuideSpan(200,200.2,1000,false).left < 200);
'''.replace("SOURCE_PATH", json.dumps(str(source)))
    result = subprocess.run([shutil.which("node") or "node", "--input-type=module", "-"],
                            input=script, text=True, cwd=runtime, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
