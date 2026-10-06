"""Independent V4 financial runner with device-resident lifecycle rule gates."""
import torch
from .search_runner import SearchRunner
from .evolution import STAGES
from .position_metrics import duration_summary

class ProgramRunner(SearchRunner):
    def run(self,**kwargs):
        if kwargs.get('reset',True) or not hasattr(self,'_report_counts'):
            self._report_counts=torch.zeros_like(self.fill_count,device='cpu')
            self._report_lots=[{} for _ in range(len(self.fill_count))]
            self._trade_totals=torch.zeros((len(self.fill_count),5),dtype=torch.float64)
            self._report_durations=[[] for _ in range(len(self.fill_count))]
            self._holding_totals=torch.zeros((len(self.fill_count),2),dtype=torch.float64)
        result=super().run(**kwargs)
        self.live_metrics()
        for i,name in enumerate(('closed_positions','winning_positions','losing_positions','gross_profit','gross_loss')):
            result[name]=self._trade_totals[:,i].clone()
        result['closed_position_duration_samples']=[list(values) for values in self._report_durations]
        return result

    def _update_trade_report(self):
        counts=self.fill_count.detach().cpu()
        delta=counts-self._report_counts
        maximum=int(delta.max())
        if maximum:
            offsets=torch.arange(maximum,device=self.ledger.device)[None]+self._report_counts.to(self.ledger.device)[:,None]
            selected=self.ledger.gather(1,offsets.clamp_max(self.ledger.shape[1]-1)[...,None].expand(-1,-1,9)).detach().cpu()
            for lane,count in enumerate(delta.tolist()):
                for row in selected[lane,:count].tolist():
                    stamp,ticker,slot,side,qty,price,fee,_,_=row
                    key=(int(ticker),int(slot));lot=self._report_lots[lane].setdefault(key,[0.,0.,0.])
                    if side==1 and lot[0]==0:lot[1]=0.;lot[2]=stamp
                    if side==-1:
                        self._holding_totals[lane,0]+=qty*(stamp-lot[2]);self._holding_totals[lane,1]+=qty
                    lot[0]+=side*qty;lot[1]+=-side*qty*price-fee
                    if side==-1 and lot[0]==0:
                        pnl=lot[1];totals=self._trade_totals[lane]
                        totals[0]+=1;totals[1]+=pnl>0;totals[2]+=pnl<0
                        totals[3]+=max(pnl,0.);totals[4]+=max(-pnl,0.)
                        self._report_durations[lane].append(int(stamp-lot[2]))
            # CPU eager readers otherwise alias the mutable fill-count tensor.
            self._report_counts=counts.clone()

    def live_metrics(self):
        """One bounded population transfer at the existing progress barrier.

        Provisional marked equity is monitoring evidence, never fitness.
        """
        if not hasattr(self,'_report_counts'):
            self._report_counts=torch.zeros_like(self.fill_count,device='cpu');self._report_lots=[{} for _ in range(len(self.fill_count))];self._trade_totals=torch.zeros((len(self.fill_count),5),dtype=torch.float64)
            self._report_durations=[[] for _ in range(len(self.fill_count))];self._holding_totals=torch.zeros((len(self.fill_count),2),dtype=torch.float64)
        self._update_trade_report()
        trades=self._trade_totals.sum(0);closed,wins,losses,profit,loss=trades.tolist()
        values=torch.stack((self.equity-self.settings.initial_cash,self.drawdown,
            (self.quantity>0).sum((1,2)),self.fill_count,self.financial_error,self.overflow)).detach().cpu()
        pnl,drawdown,opened,fills,errors,overflow=values
        # CUDA replay advances the device index before the host completed cursor
        # is finalized. Read that index at this existing monitoring barrier.
        index=int(self.index.detach().cpu())
        stamp=int(self.tape.clocks[min(max(index-1,0),len(self.tape.clocks)-1)])
        ages=[stamp-lot[2] for lots in self._report_lots for lot in lots.values() if lot[0]>0]
        holding,sold=self._holding_totals.sum(0).tolist()
        return dict(**duration_summary([v for lane in self._report_durations for v in lane]),
            **duration_summary(ages,'open_age'),weighted_hold_seconds=holding/sold if sold else None,
            pnl_min=float(pnl.min()),pnl_median=float(pnl.median()),pnl_max=float(pnl.max()),
            drawdown_max=float(drawdown.max()),open_positions_max=int(opened.max()),
            fills_max=int(fills.max()),financial_error_candidates=int(errors.count_nonzero()),
            overflow_candidates=int(overflow.count_nonzero()),closed_positions=int(closed),winning_positions=int(wins),losing_positions=int(losses),
            position_win_rate=wins/closed if closed else None,profit_factor=profit/loss if loss else None,
            gross_profit=profit,gross_loss=loss,scope='active session population; pooled closed positions; marked equity provisional')

    def __init__(self,tape,space,individuals,gates,**kwargs):
        self.native_programs=True
        self.program_gates=gates
        shape=(len(tape.clocks),len(individuals),len(tape.tickers))
        valid=(gates.shape==shape and gates.dtype==torch.uint8 and gates.device==tape.device) if isinstance(gates,torch.Tensor) else (set(gates)==set(STAGES) and all(v.shape==shape and v.dtype==torch.bool and v.device==tape.device for v in gates.values()))
        if not valid:
            raise ValueError('Require all lifecycle gates on the tape device')
        super().__init__(tape,space,[v.policy for v in individuals],**kwargs)

    def _program_gate(self,stage):
        if isinstance(self.program_gates,torch.Tensor):
            return self.program_gates.index_select(0,self.index.reshape(1)).squeeze(0).bitwise_and(1<<STAGES.index(stage))!=0
        return self.program_gates[stage].index_select(0,self.index.reshape(1)).squeeze(0)

    def _entry_filter(self,now,close):return self._program_gate('entry')

    def set_population(self,individuals,gates):
        self.set_genomes([v.policy for v in individuals])
        if isinstance(self.program_gates,torch.Tensor):
            if not isinstance(gates,torch.Tensor) or gates.shape!=self.program_gates.shape:raise ValueError('Packed gate allocation changed')
            self.program_gates.copy_(gates);return
        for stage in STAGES:
            if gates[stage].shape!=self.program_gates[stage].shape:raise ValueError('Program gate allocation changed')
            self.program_gates[stage].copy_(gates[stage])
