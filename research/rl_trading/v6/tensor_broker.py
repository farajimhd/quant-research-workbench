"""Versioned approximate 100 ms broker; listing arithmetic stays on device.

This is participation/VWAP execution, not the quote-IOC reference OMS. Passive
target capacity is an estimate from eligible bucket volume, not queue evidence.
Only time and recurrent policy events are ordered; there is no ticker loop.
"""
from dataclasses import dataclass,replace
import math
import torch
from research.rl_trading.v6.luld import RiskPenalty

VERSION='rl-v6-tensor-participation-100ms-v1'


@dataclass(frozen=True)
class BrokerConfig:
    initial_cash: float=10000.
    participation: float=.1
    price_increment: float=.0001
    max_mark_age_us: int=5_000_000
    risk: RiskPenalty=RiskPenalty()
    outside_macd_per_minute: float=0.

    def __post_init__(self):
        if not (math.isfinite(self.initial_cash) and self.initial_cash>0 and
                0<self.participation<=1 and self.price_increment>0 and
                self.max_mark_age_us>0 and math.isfinite(self.outside_macd_per_minute) and self.outside_macd_per_minute>=0):
            raise ValueError('Invalid tensor broker assumptions')


@dataclass(frozen=True)
class BrokerBucket:
    """All fields [N], except clock (host integer known by the clock loop).

    Prices are dollars float64; capacity is eligible execution volume. Missing
    source rows must have valid=False, never fabricated zero-volume evidence.
    paused is a causal modeled-LULD sidecar projection, not an official halt.
    """
    clock_us: int
    vwap: torch.Tensor
    volume: torch.Tensor
    high: torch.Tensor
    low: torch.Tensor
    spread: torch.Tensor
    valid: torch.Tensor
    extremes_valid: torch.Tensor
    quote_valid: torch.Tensor
    paused: torch.Tensor
    band_low: torch.Tensor | None=None
    band_high: torch.Tensor | None=None
    bid: torch.Tensor | None=None
    ask: torch.Tensor | None=None
    quote_timestamp_us: torch.Tensor | None=None
    volume_coverage: torch.Tensor | None=None  # [N] verified producer coverage, not trade presence.


@dataclass(frozen=True)
class TensorObservation:
    account: torch.Tensor  # [7] float32, existing policy contract.
    held_index: torch.Tensor  # [H] int64, ascending stable identity.
    held_features: torch.Tensor  # [H,11] float32.
    enter_allowed: torch.Tensor  # [N] bool.
    exit_allowed: torch.Tensor  # [H] bool.
    stop_allowed: torch.Tensor  # [H] bool.
    target_allowed: torch.Tensor  # [H] bool.
    pending_index: torch.Tensor  # [P] int64.
    execution_features: torch.Tensor | None=None  # [N,11] immutable causal snapshot.


@dataclass(frozen=True)
class TensorOutcomes:
    """Sparse actual events [K], ordered by ascending listing within a bucket.

    order_quantity is the original intent size, so repeated partial fills do
    not accidentally use their decreasing remainder as the denominator.
    """
    listing: torch.Tensor
    action: torch.Tensor
    requested_fraction: torch.Tensor
    filled_fraction: torch.Tensor
    net_over_equity: torch.Tensor
    shares: torch.Tensor | None=None
    price: torch.Tensor | None=None
    fee: torch.Tensor | None=None
    clock: torch.Tensor | None=None
    net_pnl: torch.Tensor | None=None
    position_closed: torch.Tensor | None=None
    active: torch.Tensor | None=None  # Dense compiled step; compact once per policy clock.


def order_fee(quantity,notional,sell):
    """Vectorized existing IBKR fixed research scenario, cumulative per order.

    An order's next partial-fill fee is total(after)-total(before), avoiding a
    new minimum commission on every 100 ms partial fill.
    """
    commission=torch.minimum(torch.maximum(quantity*.005,
        torch.ones_like(notional)),notional*.01)
    return torch.where(quantity>0,commission+quantity*.000003+
        sell*(notional*.0000206+torch.minimum(quantity*.000195,
            torch.full_like(notional,9.79))),torch.zeros_like(notional))


class TensorBroker:
    def __init__(self,listings,*,device,config=BrokerConfig()):
        if listings<1:
            raise ValueError('No tensor broker identities')
        self.n,self.device,self.config=listings,torch.device(device),config
        z=torch.zeros(listings,device=device,dtype=torch.float64)
        q=torch.zeros(listings,device=device,dtype=torch.int64)
        self.quantity=q.clone();self.remaining=q.clone();self.order_quantity=q.clone()
        self.side=q.clone();self.entry_us=q.clone();self.mark_us=q.clone()
        self.cost=z.clone();self.entry_fee=z.clone();self.stop=z.clone();self.target=z.clone()
        self.entry_stop_bps=z.clone();self.entry_target_bps=z.clone()
        self.mark=z.clone();self.cap=z.clone();self.fraction=z.clone()
        self.order_filled=q.clone();self.order_notional=z.clone()
        self.position_net=z.clone();self.pause_age=z.clone()
        self.paused=torch.zeros(listings,device=device,dtype=torch.bool)
        self.cash=z.new_tensor(config.initial_cash);self.realized=z.new_zeros(())
        self.fees=z.new_zeros(());self.shaping=z.new_zeros(())
        self.latest_us=q.new_zeros(());self.clock_us=None
        self.closed=q.new_zeros(());self.wins=q.new_zeros(());self.buy_fills=q.new_zeros(())
        self.sell_fills=q.new_zeros(());self.ambiguous=q.new_zeros(())
        self.turnover=z.new_zeros(());self.holding_seconds=z.new_zeros(())
        self._compiled_step=None;self._device_clock=q.new_zeros(())
        self.expose_cost_features=False
        from collections import deque
        self.cost_history=deque(maxlen=10)
        self.outside_macd=torch.zeros(listings,device=device,dtype=torch.bool)
        self.outside_macd_penalty=z.new_zeros(())

    def observe_macd(self,index,line,signal,available):
        """[K] completed-candle indicators; missing evidence is not a negative regime."""
        self.outside_macd.index_copy_(0,index,available.bool()&(line<=signal))

    def compile_step(self):
        """Opt-in fixed-shape execution kernels; compilation cost is separate.

        No dynamic holding axis or nonzero occurs inside this graph. Cash and
        liquidity remain this broker's own state; independent runs never share it.
        """
        self._compiled_step=torch.compile(self._advance_impl,fullgraph=True,dynamic=False)

    def equity(self):
        return self.cash+(self.quantity*self.mark).sum()

    def reserved(self):
        return torch.where(self.side==1,self.remaining*self.cap,0.).sum()

    def update_marks(self,index,price,clock_us):
        # [K] completed candle prices, not execution-window future marks.
        self.mark.index_copy_(0,index,price.to(torch.float64))
        self.mark_us.index_fill_(0,index,clock_us)

    def observe(self,clock_us,changed_valid):
        held=torch.nonzero(self.quantity>0).flatten()
        pending=torch.nonzero((self.side==1)&(self.remaining>0)).flatten()
        eq=self.equity();reserve=self.reserved()
        average=self.cost/self.quantity.clamp_min(1)
        age=torch.where(self.latest_us>0,(clock_us-self.latest_us)/1e6,
                        torch.zeros_like(self.cash))
        account=torch.stack((self.cash,eq,self.realized,
            (self.quantity*self.mark).sum()/eq.clamp_min(1e-9),age,reserve,
            pending.new_tensor(pending.numel()).to(torch.float64))).float()
        fields=torch.stack((self.quantity.double(),average,(clock_us-self.entry_us)/1e6,
            (self.mark-average)/average.clamp_min(1e-9),
            torch.where(self.stop>0,(self.mark-self.stop)/self.mark.clamp_min(1e-9),0.),
            torch.where(self.target>0,(self.target-self.mark)/self.mark.clamp_min(1e-9),0.),
            (self.stop>0).double(),(self.target>0).double(),(self.side==2).double(),
            self.paused.double(),torch.log1p(self.pause_age)/10),dim=1)[held].float()
        fresh=(self.mark>0)&(clock_us-self.mark_us<=self.config.max_mark_age_us)
        enter=changed_valid&fresh&(self.quantity==0)&(self.remaining==0)&~self.paused&(self.cash-reserve>0)
        exit_allowed=(self.side[held]<2)&fresh[held]
        costs=None
        if self.expose_cost_features:
            from research.rl_trading.v6.execution_features import execution_estimates,EXECUTION_NAMES
            costs=self.mark.new_zeros((self.n,len(EXECUTION_NAMES))).float()
            if len(self.cost_history)==10:
                last=self.cost_history[-1]
                if last.bid is not None and last.ask is not None and last.quote_timestamp_us is not None:
                    valid=torch.stack([b.valid for b in self.cost_history])
                    executable=valid & ~torch.stack([b.paused for b in self.cost_history])
                    executable &= torch.stack([b.quote_valid for b in self.cost_history])
                    executable &= torch.stack([(b.clock_us-b.quote_timestamp_us<=1_000_000)&
                        (b.clock_us>=b.quote_timestamp_us)&(b.bid>0)&(b.ask>=b.bid) for b in self.cost_history])
                    volumes=torch.stack([b.volume for b in self.cost_history])*executable
                    total=volumes.sum(0)
                    vwap=(volumes*torch.stack([b.vwap for b in self.cost_history])).sum(0)/total.clamp_min(1e-12)
                    capacity=torch.floor(volumes*self.config.participation).sum(0)
                    # Select latest causal quote across ten completed buckets;
                    # an event-free last bucket must not erase a fresh quote.
                    stamps=torch.stack([b.quote_timestamp_us for b in self.cost_history])
                    quote_masks=torch.stack([b.quote_valid for b in self.cost_history]) & (stamps<=clock_us)
                    index=torch.where(quote_masks,stamps,0).argmax(0,keepdim=True)
                    pick=lambda field:torch.stack([getattr(b,field) for b in self.cost_history]).gather(0,index).squeeze(0)
                    age=(clock_us-pick('quote_timestamp_us'))/1e6
                    coverage=(torch.stack([b.volume_coverage for b in self.cost_history]).all(0)
                        if all(b.volume_coverage is not None for b in self.cost_history) else valid.all(0))
                    costs=execution_estimates(self.mark,pick('bid'),pick('ask'),vwap,total,quote_masks.any(0),
                        coverage,age,
                        participation=self.config.participation,capacity=capacity)
            # Availability flags inform policy; fills retain the existing
            # quote/volume gates. Exposing features does not change actions.
        return TensorObservation(account,held,fields,enter,exit_allowed,
            exit_allowed&(self.stop[held]==0),exit_allowed&(self.target[held]==0),pending,costs)

    def _events(self,mask,action,filled,net,*,requested=None,price=None,fee=None,clock=None,dense=False):
        index=torch.arange(self.n,device=self.device) if dense else torch.nonzero(mask).flatten()
        return TensorOutcomes(index,action[index],
            (self.fraction if requested is None else requested)[index].float(),
            (filled/self.order_quantity.clamp_min(1))[index].float(),
            (net/self.equity().clamp_min(1))[index].float(),filled[index],
            (self.mark if price is None else price)[index],
            (torch.zeros_like(self.mark) if fee is None else fee)[index],
            torch.ones_like(index)*(clock if clock is not None else 0),net[index],
            ((self.quantity==0)&(action==2))[index],mask if dense else None)

    def submit(self,token,parameter,held,clock_us,*,brackets_bps=None):
        """One sampled proposal; token/parameter scalars remain on device."""
        h=held.numel();n=self.n
        # Appended ticker HOLD tokens are no orders, with no cash or memory mutation.
        enter=(token>0)&(token<=n);held_action=(token>=1+n)&(token<1+n+3*h)
        group=torch.div(token-1-n,max(h,1),rounding_mode='floor')
        slot=torch.remainder(token-1-n,max(h,1))
        padded=torch.cat((held,held.new_zeros(1)))
        listing=torch.where(enter,(token-1).clamp(0,n-1),padded.gather(0,slot.reshape(1)).squeeze(0))
        action=torch.where(enter,1,torch.where(held_action,group+2,0))
        selected=torch.arange(n,device=self.device)==listing
        free=(self.cash-self.reserved()).clamp_min(0)
        # Reserve the entire requested cash budget; fee is bounded within it.
        caps=torch.where(self.mark<1,40000,torch.where(self.mark<=5,35000,
            torch.where(self.mark<=10,30000,torch.where(self.mark<=20,25000,
            torch.where(self.mark<=50,20000,15000)))))
        requested=torch.minimum(torch.floor(free*parameter/self.mark.clamp_min(1e-9)).long(),caps)
        buy=selected&(action==1)&(self.quantity==0)&(self.remaining==0)&(self.mark>0)&(requested>0)
        if brackets_bps is not None:
            stop_distance,target_distance=brackets_bps
            if stop_distance.shape!=(n,) or target_distance.shape!=(n,):raise ValueError('Entry bracket axes differ')
            # Predicted distances are not executable until an actual fill.
            self.entry_stop_bps=torch.where(buy,stop_distance.double(),self.entry_stop_bps)
            self.entry_target_bps=torch.where(buy,target_distance.double(),self.entry_target_bps)
        sell=selected&(action==2)&(self.quantity>0)&(self.side<2)
        average=self.cost/self.quantity.clamp_min(1)
        proposed=average*torch.exp(torch.where(action==3,-parameter,parameter).double())
        proposed=torch.floor(proposed/self.config.price_increment+1e-9)*self.config.price_increment
        stop=selected&(action==3)&(self.quantity>0)&(self.stop==0)&(self.side<2)&(proposed>0)&(proposed<average)
        target=selected&(action==4)&(self.quantity>0)&(self.target==0)&(self.side<2)&(proposed>average)&torch.isfinite(proposed)
        new=buy|sell
        self.order_quantity=torch.where(buy,requested,torch.where(sell,self.quantity,self.order_quantity))
        self.remaining=torch.where(new,self.order_quantity,self.remaining)
        self.side=torch.where(buy,1,torch.where(sell,2,self.side))
        self.cap=torch.where(buy,free*parameter/self.order_quantity.clamp_min(1),torch.where(sell,0.,self.cap))
        self.fraction=torch.where(buy,parameter.double(),torch.where(sell,1.,self.fraction))
        self.order_filled=torch.where(new,0,self.order_filled)
        self.order_notional=torch.where(new,0.,self.order_notional)
        self.stop=torch.where(stop,proposed,self.stop);self.target=torch.where(target,proposed,self.target)
        immediate=stop|target
        self.latest_us=torch.where(immediate.any(),self.latest_us.new_tensor(clock_us),self.latest_us)
        codes=torch.where(stop,3,4).long()
        return self._events(immediate,codes,torch.zeros_like(self.quantity),
            torch.zeros_like(self.mark),requested=torch.zeros_like(self.mark),price=proposed,clock=clock_us)

    def advance(self,b:BrokerBucket,*,dense=False):
        """One chronological 100 ms substep, all listing updates vectorized."""
        if self.clock_us is not None and b.clock_us-self.clock_us!=100_000:
            raise ValueError('Broker must consume consecutive 100 ms boundaries')
        clock=b.clock_us
        if self._compiled_step is not None:
            self._device_clock.fill_(clock)
            outcome=self._compiled_step(replace(b,clock_us=self._device_clock),dense=True)
            if not dense:
                outcome=compact_outcomes(outcome)
        else:
            outcome=self._advance_impl(b,dense=dense)
        self.clock_us=clock
        if self.expose_cost_features:self.cost_history.append(b)
        return outcome

    def _advance_impl(self,b,*,dense=False):
        elapsed=.1
        old=self.quantity;eq=self.equity().clamp_min(1.)
        self.shaping+=((old*self.mark/eq)*(b.paused&~self.paused)).sum()*self.config.risk.halt_entry
        self.shaping+=((old*self.mark/eq)*b.paused).sum()*self.config.risk.halt_per_minute*elapsed/60
        regime_penalty=((old*self.mark/eq)*self.outside_macd).sum()*self.config.outside_macd_per_minute*elapsed/60
        self.outside_macd_penalty+=regime_penalty;self.shaping+=regime_penalty
        self.pause_age=torch.where(b.paused,self.pause_age+elapsed,0.)
        self.paused=b.paused
        capacity=torch.floor(b.volume*self.config.participation).long()
        valid=b.valid&b.quote_valid&~b.paused&(b.volume>0)&(b.vwap>0)
        # Observed half-spread is added once; no assumed spread/slippage term.
        buy_price=b.vwap+b.spread*.5;sell_price=(b.vwap-b.spread*.5).clamp_min(self.config.price_increment)
        average=self.cost/old.clamp_min(1)
        stop=(old>0)&(self.stop>0)&b.extremes_valid&(b.low<=self.stop)
        target=(old>0)&(self.target>0)&b.extremes_valid&(b.high>=self.target)
        self.ambiguous+=(stop&target).sum()
        # Stop wins ambiguous buckets, but cannot fill until a later bucket.
        trigger=stop&(self.side!=2)
        passive=target&~stop&(self.side!=2)&valid
        sell_price=torch.where(passive,torch.maximum(sell_price,self.target),sell_price)
        # A target above the observed high has no modeled executable capacity.
        passive &= sell_price<=b.high
        if b.band_low is not None:
            passive &= (sell_price>=b.band_low)&(sell_price<=b.band_high)
        buying=(self.side==1)&valid&(buy_price<=self.cap)&~stop&~target
        buy=torch.where(buying,torch.minimum(self.remaining,capacity),0)
        # Cash reservation protects simultaneous orders; incremental fees must
        # also fit each order's reserved remainder, without portfolio rescaling.
        budget=self.remaining*self.cap
        for _ in range(3):
            new_notional=self.order_notional+buy*buy_price
            fee=order_fee(self.order_filled+buy,new_notional,False)-order_fee(self.order_filled,self.order_notional,False)
            buy=torch.where(buy*buy_price+fee<=budget+1e-9,buy,(buy-1).clamp_min(0))
        fee=order_fee(self.order_filled+buy,self.order_notional+buy*buy_price,False)-order_fee(self.order_filled,self.order_notional,False)
        buy=torch.where(buy*buy_price+fee<=budget+1e-9,buy,0)
        fee=order_fee(self.order_filled+buy,self.order_notional+buy*buy_price,False)-order_fee(self.order_filled,self.order_notional,False)
        selling=((self.side==2)|passive)&valid
        sell=torch.where(selling,torch.minimum(old,torch.minimum(
            torch.where(passive,old,self.remaining),capacity)),0)
        # Passive targets become a sell order only when they actually fill.
        new_sell=passive&(self.side<2)&(sell>0)
        filled_before=torch.where(new_sell,0,self.order_filled)
        notional_before=torch.where(new_sell,0.,self.order_notional)
        sell_fee=order_fee(filled_before+sell,notional_before+sell*sell_price,True)-order_fee(filled_before,notional_before,True)
        entry_allocation=self.entry_fee*sell/old.clamp_min(1)
        net=sell*(sell_price-average)-sell_fee-entry_allocation
        self.cash+=(sell*sell_price-sell_fee-buy*buy_price-fee).sum()
        self.realized+=net.sum();self.fees+=fee.sum()+sell_fee.sum()
        self.turnover+=(buy*buy_price+sell*sell_price).sum()
        self.buy_fills+=(buy>0).sum();self.sell_fills+=(sell>0).sum()
        self.cost+=buy*buy_price-sell*average
        self.entry_fee+=fee-entry_allocation
        self.position_net+=net
        self.quantity=old+buy-sell
        # Arm at the first fill close, so earlier extrema in this same bucket
        # cannot trigger new children. Keep intent distances fixed through
        # partial entry fills while recomputing prices from actual cost basis.
        average_after=self.cost/self.quantity.clamp_min(1)
        attach=(buy>0)&(self.entry_stop_bps>0)&(self.entry_stop_bps<10000)&(self.entry_target_bps>0)
        stop_price=torch.floor(average_after*(1-self.entry_stop_bps/10000)/self.config.price_increment+1e-9)*self.config.price_increment
        target_price=torch.floor(average_after*(1+self.entry_target_bps/10000)/self.config.price_increment+1e-9)*self.config.price_increment
        valid_children=attach&(stop_price>0)&(stop_price<average_after)&(target_price>average_after)
        self.stop=torch.where(valid_children,stop_price,self.stop)
        self.target=torch.where(valid_children,target_price,self.target)
        self.entry_us=torch.where((old==0)&(buy>0),b.clock_us,self.entry_us)
        closed=(old>0)&(self.quantity==0)
        self.holding_seconds+=torch.where(closed,(b.clock_us-self.entry_us)/1e6,0.).sum()
        self.closed+=closed.sum();self.wins+=(closed&(self.position_net>0)).sum()
        self.position_net=torch.where(closed,0.,self.position_net)
        self.order_quantity=torch.where(new_sell,old,self.order_quantity)
        self.remaining=torch.where(new_sell,old-sell,(self.remaining-buy-sell).clamp_min(0))
        self.side=torch.where(new_sell,3,self.side)
        self.order_filled=filled_before+buy+sell
        self.order_notional=notional_before+buy*buy_price+sell*sell_price
        self.fraction=torch.where(new_sell,1.,self.fraction)
        events=self._events((buy+sell)>0,torch.where(buy>0,1,2).long(),buy+sell,net,
            price=torch.where(buy>0,buy_price,sell_price),fee=fee+sell_fee,clock=b.clock_us,dense=dense)
        self.remaining=torch.where(trigger,self.quantity,self.remaining)
        self.side=torch.where(trigger,2,torch.where(self.remaining>0,self.side,0))
        self.order_quantity=torch.where(trigger,self.quantity,self.order_quantity)
        self.order_filled=torch.where(trigger,0,self.order_filled)
        self.order_notional=torch.where(trigger,0.,self.order_notional)
        self.fraction=torch.where(trigger,1.,self.fraction)
        self.cap=torch.where(self.side==1,self.cap,0.)
        self.stop=torch.where(self.quantity>0,self.stop,0.)
        self.target=torch.where(self.quantity>0,self.target,0.)
        self.latest_us=torch.where((buy+sell).sum()>0,b.clock_us,self.latest_us)
        return events

    def terminal_penalty(self):
        exposure=(self.quantity*self.mark).sum()/self.equity().clamp_min(1)
        self.shaping+=exposure*self.config.risk.terminal_exposure

    def force_exit(self):
        held=self.quantity>0
        self.side=torch.where(held,2,0);self.remaining=self.quantity.clone()
        self.order_quantity=self.quantity.clone();self.order_filled.zero_();self.order_notional.zero_()
        self.cap.zero_();self.fraction=torch.where(held,1.,0.)

    def summary(self):
        # Reporting only: one explicit device-to-host transfer after collection.
        values=torch.stack((self.equity()-self.config.initial_cash,self.fees,
            self.closed.double(),self.wins.double(),self.buy_fills.double(),self.sell_fills.double(),
            (self.quantity>0).sum().double(),self.shaping,self.ambiguous.double())).cpu().tolist()
        names=('modeled_net_profit','modeled_fees','closed_positions','winning_positions',
               'buy_fill_orders','sell_fill_orders','open_positions','risk_shaping_penalty','ambiguous_buckets')
        result=dict(zip(names,values));result['win_rate']=values[3]/values[2] if values[2] else None
        result['outside_macd_shaping_penalty']=float(self.outside_macd_penalty.cpu())
        result['terminally_flat']=values[6]==0;result['environment_version']=VERSION
        result['turnover_dollars']=float(self.turnover.cpu())
        result['mean_holding_seconds']=float(self.holding_seconds.cpu())/values[2] if values[2] else None
        result['fill_scenario']='approximate_100ms_vwap_spread_participation'
        return result


def compact_outcomes(events):
    if events.active is None:return events
    selected=torch.nonzero(events.active).flatten()
    return TensorOutcomes(*(getattr(events,name)[selected] for name in (
        'listing','action','requested_fraction','filled_fraction','net_over_equity',
        'shares','price','fee','clock','net_pnl','position_closed')))
