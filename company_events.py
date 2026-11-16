"""Conservative TSLA event rules v1; research signals, not AI or live advice."""
import re
import numpy as np
import pandas as pd
from collection_risk import preopen_time

VERSION='tsla-event-rules-v1'
TTL_HOURS=72
def closing_time(day):
    return (pd.Timestamp(str(pd.Timestamp(day).date()))+pd.Timedelta(hours=16)).tz_localize('America/New_York').tz_convert('UTC')

TOPICS=[('财报/业绩',r'earnings|financial results|财报|业绩'),('产量/交付',r'deliver(?:y|ies)|production|产量|交付'),
        ('产品/业务进展',r'robotaxi|cybercab|megacharger|charger|open(?:s|ed)? (?:first|new)|充电|产品|工厂'),
        ('分析师观点/估值',r'wall street|price target|valuation|分析师|估值')]
RULES=[
 ('财报超预期','buy',r'(?:earnings|eps|revenue|profit).{0,55}(?:beat|above expectations|exceed)|(?:beat|exceed).{0,35}(?:earnings|estimates)|财报超预期|业绩超预期'),
 ('财报低于预期','reduce',r'(?:earnings|eps|revenue|profit).{0,55}(?:miss|below expectations)|(?:miss).{0,35}(?:earnings|estimates)|业绩不及预期|财报不及预期'),
 ('交付超预期','buy',r'deliver(?:y|ies).{0,45}(?:beat|exceed|above expectations)|交付.{0,15}超预期'),
 ('交付不及预期','reduce',r'deliver(?:y|ies).{0,45}(?:miss|below expectations)|交付.{0,15}不及预期'),
 ('上调业绩指引','buy',r'rais(?:e|es|ed).{0,25}(?:guidance|outlook)|上调.{0,10}(?:业绩指引|营收预期)'),
 ('下调业绩指引','reduce',r'(?:cut|cuts|lower|lowers|lowered).{0,25}(?:guidance|outlook)|下调.{0,10}(?:业绩指引|营收预期)'),
 ('评级上调','buy',r'(?:tesla|tsla).{0,35}upgrad(?:e|ed)|upgrad(?:e|ed).{0,35}(?:tesla|tsla)|特斯拉.{0,12}评级上调'),
 ('评级下调','reduce',r'(?:tesla|tsla).{0,35}downgrad(?:e|ed)|downgrad(?:e|ed).{0,35}(?:tesla|tsla)|特斯拉.{0,12}评级下调'),
 ('召回或监管调查','reduce',r'\brecall(?:s|ed)?\b|\bregulatory investigation\b|召回|监管调查'),
 ('破产申请','exit',r'fil(?:e|es|ed).{0,20}for bankruptcy|申请破产'),
]


def classify(message):
    body=message['text']
    result=dict(message_id=message['id'],asset=message['asset'],text=body,url=message.get('url',''),source=message['source_name'],
                published=message['published'],available=max(message['published'],message['captured'],message['available']),
                rule_version=VERSION,event_type='未识别确定事件',signal='hold',evidence=[],reason='没有匹配到可操作事件，保留观察')
    if message['asset']!='TSLA' or not re.search(r'\btesla\b|\btsla\b|特斯拉',body,re.I):
        result['reason']='没有明确指向特斯拉，不强行关联资产';return result
    for topic,pattern in TOPICS:
        match=re.search(pattern,body,re.I)
        if match:
            result['event_type']=topic+'（影响未确定）'
            result['evidence']=[body[max(0,match.start()-25):min(len(body),match.end()+25)]]
            result['reason']='识别到具体公司事件，但缺少可确认的超预期、风险严重度或价格依据，不自动买卖'
            break
    if message.get('category') in ('social','group') or message.get('content_type')=='promo':
        result['reason']='来源或推广性质不满足自动模拟决策条件';return result
    if re.search(r'\b(?:nvidia|apple|rivian|ford|uber|waymo)\b|英伟达|苹果公司|竞争对手',body,re.I):
        result['reason']='涉及其他公司，事件主体可能混淆，需人工核验';return result
    if re.search(r'\b(?:may|might|could|should|expected to|rumou?r|if|not|no)\b|tesla rival|预测|预计|或将|传闻|假如|不(?:会|再)|未发生|没有|\?',body,re.I):
        result['event_type']=result['event_type'].replace('（影响未确定）','')+' · 预期/否定/评论待核验';result['reason']='不将预测、疑问、否定句或竞争对手消息当作确定利好利空';return result
    hits=[]
    for name,signal,pattern in RULES:
        match=re.search(pattern,body,re.I)
        if match:hits.append((name,signal,body[max(0,match.start()-25):min(len(body),match.end()+25)]))
    if not hits:return result
    result['evidence']=[h[2] for h in hits]
    positive=any(h[1]=='buy' for h in hits);negative=any(h[1] in ('reduce','exit') for h in hits)
    result['event_type']=' / '.join(h[0] for h in hits)
    if positive and negative:result['reason']='同一消息利好利空冲突，需人工审核';return result
    result['signal']='exit' if any(h[1]=='exit' for h in hits) else 'reduce' if negative else 'buy'
    result['reason']='规则匹配产生候选信号，必须结合可得时间、价格信号和风控；不是AI预测或真实订单'
    return result


def event_frames(frames,records):
    events=[classify(m) for m in records if m['asset']=='TSLA']
    available=np.array([pd.Timestamp(e['available']).timestamp() for e in events])
    published=np.array([pd.Timestamp(e['published']).timestamp() for e in events])
    output={}
    for symbol,frame in frames.items():
        rows=[]
        for position,day in enumerate(frame.index):
            cutoff=preopen_time(day).timestamp()
            mask=(available<cutoff)&(published>=cutoff-TTL_HOURS*3600) if symbol=='TSLA' else np.zeros(len(events),dtype=bool)
            selected=[events[i] for i in np.flatnonzero(mask) if events[i]['signal']!='hold']
            signals={e['signal'] for e in selected}
            conflict='buy' in signals and bool(signals & {'reduce','exit'})
            factor=1. if conflict else 0. if 'exit' in signals else .5 if 'reduce' in signals else 1.
            price_after_news = bool(position and selected and all(closing_time(frame.index[position-1]).timestamp() >= pd.Timestamp(e['available']).timestamp() for e in selected if e['signal']=='buy'))
            allowed=signals=={'buy'} and price_after_news
            reason='利好利空冲突，不新增买入' if conflict else ' / '.join(sorted({e['event_type'] for e in selected})) or '无可得有效公司事件，不新增买入'
            rows.append((0.,0.,factor,allowed,reason,','.join(e['message_id'] for e in selected)))
        output[symbol]=pd.DataFrame(rows,index=frame.index,columns=['z','coverage','multiplier','entry_allowed','event_reason','event_message_ids'])
    return output


def current_events(records,now,quote_date=None,price_confirmed=False):
    clock=pd.Timestamp(now)
    fresh=bool(quote_date and closing_time(quote_date)<=clock and 0<=(clock.normalize()-pd.Timestamp(quote_date).tz_localize('UTC').normalize()).days<=3)
    output=[]
    for m in records:
        if m['asset']!='TSLA':continue
        e=classify(m)
        available=pd.Timestamp(e['available'])<=clock
        expired=clock-pd.Timestamp(e['published'])>pd.Timedelta(hours=TTL_HOURS)
        e['action']='不操作'
        if not available:e['block_reason']='消息尚不可得'
        elif expired:e['block_reason']='事件发布已超过72小时有效窗口'
        elif not fresh:e['block_reason']='缺少足够新的真实行情，不生成买卖操作'
        elif e['signal']=='buy' and closing_time(quote_date)<pd.Timestamp(e['available']):e['block_reason']='现有收盘行情早于消息首次可得时间，等待消息后的价格确认'
        elif e['signal']=='buy' and not price_confirmed:e['block_reason']='价格趋势未确认'
        else:
            e['block_reason']=e['reason']
            e['action']={'buy':'候选模拟买入','reduce':'持仓时模拟减仓','exit':'持仓时模拟退出','hold':'不操作'}[e['signal']]
        output.append(e)
    active=[e['signal'] for e in output if e['action']!='不操作']
    if 'buy' in active and any(s in active for s in ('reduce','exit')):
        for e in output:
            e['action']='不操作';e['block_reason']='当前有效消息利好利空冲突，需人工核验'
    return dict(version=VERSION,asset='TSLA',quote_date=quote_date,quote_fresh=fresh,price_confirmed=price_confirmed,
                events=sorted(output,key=lambda e:e['available'],reverse=True),note='公司新闻事件规则实验；并非AI模型或实盘建议。先核验原文；当前价格确认仅用本地真实日线，不读取实时交易账户。')
