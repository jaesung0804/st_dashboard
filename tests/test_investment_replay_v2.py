"""Clock, cash and transfer tests; run directly without adding dependencies."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from test_investment_replay import fixture_data
from ai_stock_assistant.investment_replay import prepare,replay
from ai_stock_assistant.investment_replay_v2 import replay_v2,governor_targets


def fixture():
    prices,filings,old,dates=fixture_data()
    policy=json.loads(Path('data/reference/investment_replay_v2_policy.json').read_text(encoding='utf-8'))
    policy.update(start=old['start'],end=old['end'],review_split=old['review_split'])
    return prices,filings,policy,dates


def test_baseline_reproduces_v1_when_capacity_does_not_bind():
    p,f,policy,d=fixture()
    data=prepare(p,f,policy,'us')
    original=replay(data,policy,'us')
    result=replay_v2(data,policy,'baseline')
    for name in original['series']:
        np.testing.assert_allclose(original['series'][name],result['series'][name],rtol=1e-12,atol=1e-7)


def test_future_prices_and_filings_do_not_change_prefix():
    p,f,policy,d=fixture()
    a=replay_v2(prepare(p,f,policy,'us'),policy,'efficient')
    cutoff=d[155]
    p.loc[p.date>cutoff,['open','high','low','close','adjusted_close']]*=3
    later=f.copy();later['available_at']=d[157];later['filed']=d[156];later['fin_roa']=10;later['filing_id']='future'
    b=replay_v2(prepare(p,pd.concat([f,later]),policy,'us'),policy,'efficient')
    before=lambda x:[r for r in x if r['signal_date']<=str(cutoff.date())]
    assert before(a['decisions'])==before(b['decisions'])
    count=sum(day<=str(cutoff.date()) for day in a['dates'])
    for key in a['series']:np.testing.assert_array_equal(a['series'][key][:count],b['series'][key][:count])
    assert all(t['fill_date']>t['signal_date'] for t in b['trades'])


def test_cost_delay_and_missing_price_reserve():
    p,f,policy,d=fixture()
    for col in ['open','high','low','close','adjusted_close']:p[col]=100.
    p=p.loc[~((p.ticker=='T11')&(p.date>=d[150]))]
    data=prepare(p,f,policy,'us')
    a=replay_v2(data,policy,'efficient')
    b=replay_v2(data,policy,'efficient',cost_multiplier=2,fill_delay=2)
    assert a['summary']['baseline_equal_weight']['stale_reserved_return']<a['summary']['baseline_equal_weight']['net_return']
    assert b['summary']['baseline_equal_weight']['net_return']<a['summary']['baseline_equal_weight']['net_return']
    assert not any(t['ticker']=='T11' and t['fill_date']>=str(d[150].date()) for t in a['trades'])
    assert all(pd.Timestamp(t['fill_date'])>pd.Timestamp(t['signal_date']) for t in b['trades'])


def test_governor_only_rewards_past_unit_returns_and_can_pause():
    policy={'probation_drawdown':.20}
    books={'a':{'unit_history':np.linspace(1,1.3,127).tolist()},'b':{'unit_history':np.linspace(1,1.05,127).tolist()},'c':{'unit_history':np.linspace(1,.6,127).tolist()}}
    weights,scores,paused=governor_targets(books,list(books),policy)
    assert weights=={'a':.45,'b':.45,'c':0.}
    assert paused==['c'] and scores['a']>scores['b']>scores['c']


def test_cash_transfers_preserve_company_nav_and_unitized_skill():
    p,f,policy,d=fixture()
    extended=pd.bdate_range('2022-01-03',periods=460)
    parts=[]
    for k in range(12):
        for i,date in enumerate(extended):
            price=100*(1.0002+k*.00002)**i
            parts.append(dict(date=date,ticker=f'T{k:02d}',open=price,high=price,low=price,close=price,adjusted_close=price,volume=1e7))
    policy.update(start=str(extended[210].date()),end=str(extended[-1].date()))
    data=prepare(pd.DataFrame(parts),f,policy,'us')
    result=replay_v2(data,policy,'governed')
    assert result['governance'] and result['transfers']
    assert all(r['date']>result['dates'][126] for r in result['transfers'])
    assert all(np.isfinite(x) and x>0 for x in result['series']['compound'])
    # Future suffix cannot alter earlier governance or transfers.
    prices=pd.DataFrame(parts);cutoff=extended[410]
    prices.loc[prices.date>cutoff,['open','high','low','close','adjusted_close']]*=.25
    updated=replay_v2(prepare(prices,f,policy,'us'),policy,'governed')
    for key in ['governance','transfers']:
        before=lambda rows:[r for r in rows if r['date']<=str(cutoff.date())]
        assert before(result[key])==before(updated[key])


if __name__=='__main__':
    checks=[v for k,v in list(globals().items()) if k.startswith('test_') and callable(v)]
    for check in checks:check();print('PASS',check.__name__)
    print(len(checks),'checks passed')
