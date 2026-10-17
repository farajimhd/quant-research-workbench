"""Single-pass CUDA feature projection; original clocks and numeric operations."""
import torch
import triton
import triton.language as tl
from .features import ORIGINAL
from research.vectorized_backtest.v7.features import BASE,HISTORY,PRICE_COLUMNS


@triton.jit
def divide64(a,b):
    return tl.inline_asm_elementwise('div.rn.f64 $0, $1, $2;',constraints='=d,d,d',
                                   args=[a,b],dtype=tl.float64,is_pure=True,pack=1)


@triton.jit
def gather(R,I,C,FAMILIES,COLUMNS,BASE_VALUES,BASE_VALID,HIST,FEATURE_ROW,
           MARK,BID,ASK,QUOTE,QUOTE_SECONDS,MARKET_CLOCK,LOG_VOLUME,LOG_NOTIONAL,
           LOG_COUNT,INDICATORS,OUT,MASK,rows,candles,columns,base_width,history_width,
           indicator_width,r0,r1,i0,i1,c1,BLOCK:tl.constexpr):
    col=tl.program_id(0);r=tl.program_id(1)*BLOCK+tl.arange(0,BLOCK);live=r<rows
    family=tl.load(FAMILIES+col);channel=tl.load(COLUMNS+col)
    source=tl.load(R+(r//candles)*r0+(r%candles)*r1,live,-1)
    safe=tl.maximum(source,0);known=live & (source>=0)
    value=tl.full((BLOCK,),0,tl.float32);mask=tl.full((BLOCK,),False,tl.int1)
    if family==0 or family==4:
        feature_row=tl.load(FEATURE_ROW+safe,live,-1);fr=tl.maximum(feature_row,0)
        if family==0:
            value=tl.load(BASE_VALUES+fr*base_width+channel,live,0)
            mask=tl.load(BASE_VALID+fr*base_width+channel,live,0) & known & (feature_row>=0)
        else:
            value=tl.load(INDICATORS+fr*indicator_width+channel,live,0)
            mask=(tl.abs(value)<float('inf')) & known & (feature_row>=0)
    elif family==1 or family==2:
        identity=tl.load(I+(r//candles)*i0+(r%candles)*i1,live,0)
        value=tl.load(HIST+identity*history_width+channel,live,0)
        if family==1:mask=(tl.abs(value)<float('inf')) & known
        else:
            price=tl.load(MARK+safe,live,1)
            value=(divide64(value.to(tl.float64),price)-tl.where((channel>=180)&(channel<211),0.,1.)).to(tl.float32)
            mask=(tl.abs(value)<float('inf')) & known & (price>0)
    else:
        price=tl.load(MARK+safe,live,1);bid=tl.load(BID+safe,live,0);ask=tl.load(ASK+safe,live,0)
        quote=tl.load(QUOTE+safe,live,0);clock=tl.load(C+(r%candles)*c1,live,0)
        age=clock-tl.load(QUOTE_SECONDS+safe,live,0)
        current=known & (tl.load(MARKET_CLOCK+safe,live,0)==clock)
        value64=tl.full((BLOCK,),0,tl.float64)
        if channel==0:value64=divide64(bid,price)-1.
        elif channel==1:value64=divide64(ask,price)-1.
        elif channel==2:value64=divide64(ask-bid,price)
        elif channel==3:value64=age
        elif channel==4:value64=tl.where(current,tl.load(LOG_VOLUME+safe,live,0),0.).to(tl.float64)
        elif channel==5:value64=tl.where(current,tl.load(LOG_NOTIONAL+safe,live,0),0.).to(tl.float64)
        elif channel==6:value64=tl.where(current,tl.load(LOG_COUNT+safe,live,0),0.).to(tl.float64)
        elif channel==7:value64=bid
        elif channel==8:value64=ask
        elif channel==9:value64=ask-bid
        elif channel==10:value64=price
        mask=(tl.abs(value64)<float('inf')) & known
        if channel==0 or channel==1 or channel==2 or channel==7 or channel==8 or channel==9:
            mask=mask & (quote>0) & (age>=0) & (age<=1) & (bid>0) & (ask>=bid)
        elif channel==3:mask=mask & (quote>0) & (age>=0)
        elif channel==10:mask=mask & (price>0)
        value=value64.to(tl.float32);mask=mask & (tl.abs(value)<float('inf'))
    tl.store(OUT+r*columns+col,tl.where(mask,value,0.),live)
    tl.store(MASK+r*columns+col,mask,live)


def project(resident,rows,ids,clock,columns):
    a=resident.tensors;key=('fused_projection',tuple(columns))
    if key not in resident.column_plans:
        families=[];channels=[]
        for c in columns:
            if c<len(BASE):family=0;channel=c
            elif c<len(BASE)+len(HISTORY):family=1;channel=c-len(BASE)
            elif c<len(ORIGINAL)-11:family=2;channel=PRICE_COLUMNS[c-len(BASE)-len(HISTORY)]
            elif c<len(ORIGINAL):family=3;channel=c-(len(ORIGINAL)-11)
            else:family=4;channel=c-len(ORIGINAL)
            families.append(family);channels.append(channel)
        resident.column_plans[key]=tuple(torch.tensor(v,device=resident.device,dtype=torch.int32) for v in (families,channels))
    families,channels=resident.column_plans[key]
    if any(c>=len(ORIGINAL) for c in columns) and 'indicators' not in a:
        raise ValueError('Continuous indicator bank required for period variants')
    values=torch.empty((*rows.shape,len(columns)),device=resident.device,dtype=torch.float32)
    masks=torch.empty_like(values,dtype=torch.bool)
    indicators=a.get('indicators',a['base'])
    gather[(len(columns),triton.cdiv(rows.numel(),128))](
        rows,ids,clock,families,channels,a['base'],a['base_valid'],a['history'],a['feature_row'],
        a['mark'],a['bid'],a['ask'],a['quote_us'],a['quote_seconds'],a['market_clock'],
        a['log_volume'],a['log_notional'],a['log_trade_count'],indicators,values,masks,
        rows.numel(),rows.shape[-1],len(columns),a['base'].shape[-1],a['history'].shape[-1],
        indicators.shape[-1],*rows.stride(),*ids.stride(),clock.stride(-1),BLOCK=128,enable_fp_fusion=False)
    return values,masks
