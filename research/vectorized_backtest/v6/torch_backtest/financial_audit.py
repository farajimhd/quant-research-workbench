"""Independent cash/quantity reconciliation from actual fill receipts."""
import math
import torch


def audit_fills(path,metrics,*,initial_cash=10000.):
    saved=torch.load(path,map_location='cpu',weights_only=True)
    ledger,counts=saved['ledger'],saved['counts']
    if ledger.ndim!=3 or ledger.shape[2]!=9 or counts.shape!=(ledger.shape[0],):
        raise ValueError('Malformed financial ledger')
    reports=[]
    for lane,count in enumerate(counts.tolist()):
        if count<0 or count>ledger.shape[1] or count!=metrics['fill_count'][lane]:
            raise ValueError('Financial fill count mismatch')
        cash=initial_cash;fees=sold=hold=0.;lots={};opened=0;closed=[];durations=[];previous=-math.inf
        for stamp,ticker,slot,side,quantity,price,fee,kind,index in ledger[lane,:count].tolist():
            if (not all(math.isfinite(v) for v in (stamp,ticker,slot,side,quantity,price,fee,kind,index))
                    or stamp<previous or stamp!=int(stamp) or ticker<0 or ticker!=int(ticker)
                    or not 0<=slot<15 or slot!=int(slot) or side not in (-1.,1.)
                    or quantity<=0 or quantity!=int(quantity) or price<=0 or fee<0):
                raise ValueError('Invalid financial fill value/order')
            previous=stamp;key=(int(ticker),int(slot));lot=lots.setdefault(key,[0.,0.,0.])
            delta=-side*quantity*price-fee;cash+=delta;fees+=fee
            if side==1:
                if lot[0]==0:lot[1]=stamp;lot[2]=0.;opened+=1
                lot[0]+=quantity
            else:
                if quantity>lot[0]:raise ValueError('Sell exceeds actual position quantity')
                hold+=quantity*(stamp-lot[1]);sold+=quantity;lot[0]-=quantity
            lot[2]+=delta
            if side==-1 and lot[0]==0:closed.append(lot[2]);durations.append(int(stamp-lot[1]))
            if cash < -1e-6:raise ValueError('Financial ledger spends unavailable cash')
        quantity=sum(lot[0] for lot in lots.values());positions=sum(lot[0]>0 for lot in lots.values())
        expected=dict(cash=cash,fees=fees,open_quantity=quantity,open_positions=positions,
                      sold_shares=sold,sold_share_seconds=hold)
        if 'positions_opened' in metrics:expected['positions_opened']=opened
        if quantity==0:expected['net_pnl']=cash-initial_cash
        for name,value in expected.items():
            if name not in metrics or not math.isclose(value,metrics[name][lane],rel_tol=1e-10,abs_tol=1e-6):
                raise ValueError('Financial ledger arithmetic mismatch: '+name)
        if bool(metrics['terminal_valid'][lane])!=(quantity==0):
            raise ValueError('Terminal flatness disagrees with actual fills')
        trade=dict(closed_positions=len(closed),winning_positions=sum(v>0 for v in closed),losing_positions=sum(v<0 for v in closed),
                   gross_profit=sum(max(v,0.) for v in closed),gross_loss=sum(max(-v,0.) for v in closed))
        for name,value in trade.items():
            if name in metrics and not math.isclose(value,metrics[name][lane],rel_tol=1e-10,abs_tol=1e-6):
                raise ValueError('Closed-position reporting mismatch: '+name)
        if 'closed_position_duration_samples' in metrics and durations!=metrics['closed_position_duration_samples'][lane]:
            raise ValueError('Closed-position elapsed duration disagrees with actual fills')
        if 'closed_position_pnl_samples' in metrics:
            reported=metrics['closed_position_pnl_samples'][lane]
            if len(reported)!=len(closed) or any(not math.isclose(actual,saved,rel_tol=1e-10,abs_tol=1e-6) for actual,saved in zip(closed,reported)):
                raise ValueError('Closed-position P&L samples disagree with actual fills')
        reports.append(dict(**expected,**trade,position_win_rate=trade['winning_positions']/len(closed) if closed else None))
    return reports
