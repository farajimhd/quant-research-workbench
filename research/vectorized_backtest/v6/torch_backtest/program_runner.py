"""Independent V4 financial runner with device-resident lifecycle rule gates."""
import torch
from .search_runner import SearchRunner
from .evolution import STAGES
from .position_metrics import duration_summary

class ProgramRunner(SearchRunner):
    def tick(self):
        now=self.tape.clocks.index_select(0,self.index.reshape(1)).squeeze(0)
        ask,bid,quote,observed=self._row('ask'),self._row('bid'),self._row('quote_valid'),self._row('observed')
        add,reduce,exit=self._program_gate('add'),self._program_gate('reduce'),self._program_gate('exit')
        super().tick()
        self._manage_positions(now,ask,bid,quote&observed,add,reduce,exit)

    def _manage_positions(self,now,ask,bid,valid,add,reduce,exit):
        s=self.settings
        def value(name):return self.management_columns[name][:,None,None]
        live=(self.quantity>0)&self._market_value(valid,3)&(self.remaining==0)&(self.exit_kind==0)
        live&=(now-self.first_fill>=s.minimum_position_hold_seconds)&(now-self.management_at>=value('management_cooldown_seconds'))
        terminal=now>=self.end_boundary-self._value('terminal_exit_lead_seconds',3)
        live&=~terminal
        profitable=self._market_value(bid,3)>=self.average*(1+value('reduce_minimum_profit_fraction'))
        reduce_mask=live&reduce[...,None]&profitable&~exit[...,None]
        amount=torch.floor(self.quantity*value('reduce_fraction')).to(torch.int64)
        reduce_mask&=amount>0
        self.reduce_remaining.copy_(torch.where(reduce_mask,amount,self.reduce_remaining))
        self.exit_filled[...,4].copy_(torch.where(reduce_mask,0,self.exit_filled[...,4]))
        self.exit_paid[...,4].copy_(torch.where(reduce_mask,0,self.exit_paid[...,4]))
        self.exit_kind.copy_(torch.where(reduce_mask,5,self.exit_kind))
        # A full discretionary exit supersedes an unfinished profit reduction.
        full=exit[...,None]&(self.quantity>0)&(self.exit_kind==5)
        self.exit_kind.copy_(torch.where(full,3,self.exit_kind))
        self.reduce_remaining.copy_(torch.where(full,0,self.reduce_remaining))
        eligible=live&add[...,None]&~reduce_mask&~exit[...,None]&(self.exit_kind==0)
        eligible&=self.add_count<value('maximum_adds')
        eligible&=self._market_value(bid,3)>=self.average*(1+value('add_minimum_profit_fraction'))
        wanted=torch.where(eligible,torch.floor(self.quantity*value('add_fraction')).to(torch.int64),0)
        limit=self._market_value(ask.nan_to_num(0),3)*(1+self._value('maximum_entry_drift_fraction',3))
        pending=(self.remaining*self.buy_limit).sum((1,2))
        pending_fee=(torch.maximum(torch.full_like(self.buy_paid,s.minimum_order_fee),
            (self.buy_order_filled+self.remaining)*s.fee_per_share)-self.buy_paid).clamp_min(0)
        available=(self.cash-pending-torch.where(self.remaining>0,pending_fee,0).sum((1,2))-self._exit_fee_reserve().sum((1,2))).clamp_min(0)
        minima=(wanted>0).sum((1,2))*s.minimum_order_fee
        spend=(wanted*(limit+2*s.fee_per_share)).sum((1,2))
        scale=((available-minima).clamp_min(0)/spend.clamp_min(1e-12)).clamp(max=1)
        wanted=torch.floor(wanted*scale[:,None,None]).to(torch.int64)
        reserved=(self.quantity*(self.average-self.stop).clamp_min(0)+self.remaining*(self.buy_limit-self.stop).clamp_min(0)).sum((1,2))
        room=(self.equity.clamp_min(0)*s.maximum_stop_risk_fraction-reserved).clamp_min(0)
        risk=(wanted*(limit-self.stop).clamp_min(0)).sum((1,2))
        wanted=torch.floor(wanted*(room/risk.clamp_min(1e-12)).clamp(max=1)[:,None,None]).to(torch.int64)
        add_mask=wanted>0
        self.requested_quantity.add_(wanted);self.remaining.add_(wanted)
        for name,new in (('buy_limit',limit),('buy_reference',self._market_value(ask,3)),('buy_submitted',now),('buy_created',now),('buy_last_retry',now),
            ('buy_deadline',now+self._value('entry_deadline_seconds',3)),('buy_retries',0),('buy_order_filled',0),('buy_paid',0)):
            state=getattr(self,name);state.copy_(torch.where(add_mask,new,state))
        self.add_count.add_(add_mask.to(torch.int64))
        self.management_at.copy_(torch.where(add_mask|reduce_mask,now,self.management_at))
        new_reserved=(self.quantity*(self.average-self.stop).clamp_min(0)+self.remaining*(self.buy_limit-self.stop).clamp_min(0)).sum((1,2))
        self.peak_reserved_stop_risk.copy_(torch.maximum(self.peak_reserved_stop_risk,new_reserved))
    @staticmethod
    def specialization_key(individuals,space):
        from .genome import NAMES
        rows=space.validate([v.policy for v in individuals])
        modes=tuple(int(rows[0,i]) if (rows[:,i]==rows[0,i]).all() else -1 for i in (5,6,7,8,9))
        windows=tuple(int(rows[:,space.policy_start+NAMES.index(name)].max()) for name in
                      ('adaptive_window','swing_left_seconds','swing_right_seconds','momentum_lookback_seconds','attention_lookback_seconds'))
        zero_weights=tuple(bool((rows[:,space.policy_start+NAMES.index(name)]==0).all()) for name in
                           ('momentum_weight','attention_weight'))
        return (int(rows[:,4].max()),modes,windows,zero_weights)

    def _observe_rule_history(self,close,observed):
        if not self.specialize:return super()._observe_rule_history(close,observed)

    def run(self,**kwargs):
        if kwargs.get('reset',True) or not hasattr(self,'_report_counts'):
            self._report_counts=torch.zeros_like(self.fill_count,device='cpu')
            self._report_lots=[{} for _ in range(len(self.fill_count))]
            self._trade_totals=torch.zeros((len(self.fill_count),5),dtype=torch.float64)
            self._report_durations=[[] for _ in range(len(self.fill_count))]
            self._report_pnls=[[] for _ in range(len(self.fill_count))]
            self._holding_totals=torch.zeros((len(self.fill_count),2),dtype=torch.float64)
        result=super().run(**kwargs)
        self.live_metrics()
        for i,name in enumerate(('closed_positions','winning_positions','losing_positions','gross_profit','gross_loss')):
            result[name]=self._trade_totals[:,i].clone()
        result['closed_position_duration_samples']=[list(values) for values in self._report_durations]
        result['closed_position_pnl_samples']=[list(values) for values in self._report_pnls]
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
                        self._report_pnls[lane].append(pnl)
            # CPU eager readers otherwise alias the mutable fill-count tensor.
            self._report_counts=counts.clone()

    def live_metrics(self):
        """One bounded population transfer at the existing progress barrier.

        Provisional marked equity is monitoring evidence, never fitness.
        """
        if not hasattr(self,'_report_counts'):
            self._report_counts=torch.zeros_like(self.fill_count,device='cpu');self._report_lots=[{} for _ in range(len(self.fill_count))];self._trade_totals=torch.zeros((len(self.fill_count),5),dtype=torch.float64)
            self._report_durations=[[] for _ in range(len(self.fill_count))];self._holding_totals=torch.zeros((len(self.fill_count),2),dtype=torch.float64)
            self._report_pnls=[[] for _ in range(len(self.fill_count))]
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

    def __init__(self,tape,space,individuals,gates,*,specialize=True,**kwargs):
        from .evolution import MANAGEMENT_BOUNDS
        self.management_columns={}
        for name,(lo,hi,integer) in MANAGEMENT_BOUNDS.items():
            values=[v.management[name] for v in individuals]
            if any(not lo<=v<=hi or (integer and int(v)!=v) for v in values):raise ValueError('Invalid management parameter: '+name)
            self.management_columns[name]=torch.tensor(values,dtype=torch.float64,device=tape.device)
        self.native_programs=True
        self.specialize=specialize
        kwargs.setdefault('masked_ledger',specialize)
        self.execution_key=self.specialization_key(individuals,space) if specialize else None
        if specialize:kwargs.setdefault('slot_capacity',self.execution_key[0])
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
        if self.specialize and self.specialization_key(individuals,self.space)!=self.execution_key:
            raise ValueError('Changing execution specialization requires a new runner')
        self.set_genomes([v.policy for v in individuals])
        from .evolution import MANAGEMENT_BOUNDS
        for name,(lo,hi,integer) in MANAGEMENT_BOUNDS.items():
            values=[v.management[name] for v in individuals]
            if any(not lo<=v<=hi or (integer and int(v)!=v) for v in values):raise ValueError('Invalid management parameter: '+name)
            self.management_columns[name].copy_(torch.tensor(values,dtype=torch.float64,device=self.tape.device))
        if gates is self.program_gates:return
        if isinstance(self.program_gates,torch.Tensor):
            if not isinstance(gates,torch.Tensor) or gates.shape!=self.program_gates.shape:raise ValueError('Packed gate allocation changed')
            if gates is not self.program_gates:self.program_gates.copy_(gates)
            return
        for stage in STAGES:
            if gates[stage].shape!=self.program_gates[stage].shape:raise ValueError('Program gate allocation changed')
            self.program_gates[stage].copy_(gates[stage])
