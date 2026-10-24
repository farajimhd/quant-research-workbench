import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import io,unittest
from rich.console import Console
from research.vectorized_backtest.v48.observer_model import enrich,ordered,grouped
from research.vectorized_backtest.v48.progress_view import render,visible
from research.vectorized_backtest.v48.progress import Controls


def fixture():
    a='2026-08-13:security:a';b='2026-08-13:security:b';owners=[a,a,b,b]
    values=[(120.,30.,100.,100.,True,3),(0.,0.,0.,0.,True,0),(70.,100.,200.,10.,True,5),(999.,0.,0.,999.,False,2)]
    rows=[]
    for i,(pnl,loss,dd,score,eligible,trades) in enumerate(values):
        rows.append(dict(serial=i,rank=99,score=score,eligible=eligible,metrics=dict(net_pnl=pnl,closed_positions=trades,win_rate=.5 if trades else None,maximum_position_risk=50. if eligible else 400.)))
    scores=dict(ids=list(range(4)),objective_diagnostics=dict(pnl=[r[0] for r in values],gross_losses=[r[1] for r in values],session_realized_drawdown=[r[2] for r in values],case_owners=owners))
    labels={a:'AAPL',b:'XHG'}
    return dict(rows=enrich(rows,scores,labels),case_labels=labels,next_quotas={a:700,b:324},phase=0,running=True,day='2026-08-13',generation=2,target=30,
                session_progress=dict(phase=0,status='running',generation=3,stage='Replay positions',completed_local_batches=0,total_local_batches=1,completed_seconds=500,total_seconds=19799))


class ObserverTests(unittest.TestCase):
    def test_loading_never_claims_empty_and_keeps_last_verified_panel(self):
        import threading
        from research.vectorized_backtest.v48.observer_model import Observer
        observer=Observer.__new__(Observer);observer.lock=threading.Lock()
        observer.data=dict(generation=5,rows=[dict(score=1)],session_progress={'phase':0});observer.display_snapshot={}
        self.assertFalse(observer.read()['display_ready'])
        observer.display_snapshot=dict(fixture(),display_ready=True)
        self.assertEqual(len(observer.read()['rows']),4)
        self.assertTrue(observer.read()['display_ready'])

    def test_ticker_summary_excludes_ineligible_leader(self):
        data=fixture();g={x['ticker']:x for x in grouped(data['rows'],data['next_quotas'])}
        self.assertEqual(g['XHG']['score'],10.);self.assertEqual(g['XHG']['best_serial'],2)
        self.assertEqual(g['AAPL']['count'],2);self.assertEqual(g['AAPL']['next_slots'],700)
        self.assertEqual(sum(x['score'] for x in g.values()),110.)

    def test_sorts_directions_and_missing_win_rate(self):
        rows=fixture()['rows'];self.assertEqual(ordered(rows,'score')[0]['serial'],3)
        self.assertEqual(ordered(rows,'loss')[0]['serial'],1)
        self.assertEqual(ordered(rows,'loss',True)[0]['serial'],2)
        self.assertEqual(ordered(rows,'win')[-1]['serial'],1)
        self.assertEqual(ordered(rows,'positions')[0]['serial'],2)

    def test_ticker_navigation_filters_and_enter(self):
        data=fixture();c=Controls();c.key('g',data)
        self.assertTrue(c.settings['grouped']);c.key('\r',data)
        self.assertFalse(c.settings['grouped']);self.assertEqual(len(visible(data,c.settings)),2)
        c.key('0',data);c.key('z',data);c.key('c',data)
        self.assertEqual(len(visible(data,c.settings)),2)
        c.key('3',data);self.assertEqual(c.settings['sort'],'loss')
        c.key(']',data);self.assertEqual(c.settings['case'],'2026-08-13:security:a')
        c.key('[',data);self.assertEqual(c.settings['case'],'2026-08-13:security:b')

    def test_enrichment_rejects_cost_mismatch(self):
        data=fixture();rows=data['rows']
        bad=dict(ids=[0,1,2,3],objective_diagnostics=dict(pnl=[1]*4,gross_losses=[0]*4,session_realized_drawdown=[0]*4,case_owners=[None]*4))
        with self.assertRaisesRegex(ValueError,'cost'):enrich(rows,bad,{})

    def test_normal_compact_and_short_render_bounds(self):
        for width,height in ((120,38),(80,24),(65,20)):
            for group in (False,True):
                stream=io.StringIO();console=Console(file=stream,width=width,height=height,color_system=None,force_terminal=False)
                settings=dict(sort='score',grouped=group)
                console.print(render(fixture(),width,height,settings))
                output=stream.getvalue();lines=output.splitlines()
                self.assertEqual(len(lines),height);self.assertTrue(all(len(line)<=width for line in lines))
                self.assertIn('V48',output);self.assertIn('Score',output);self.assertIn('Q close',output)
                self.assertNotIn('V47',output);self.assertNotIn('1 profit',output)

    def test_startup_stale_empty_and_phase1(self):
        for data in (dict(fixture(),rows=[],generation=0),dict(fixture(),display_error='Receipt temporarily unavailable'),dict(fixture(),rows=[],phase=1,target=20)):
            stream=io.StringIO();console=Console(file=stream,width=100,height=30,color_system=None)
            console.print(render(data,100,30,dict(sort='score')))
            self.assertIn('V48',stream.getvalue())


if __name__=='__main__':unittest.main()
