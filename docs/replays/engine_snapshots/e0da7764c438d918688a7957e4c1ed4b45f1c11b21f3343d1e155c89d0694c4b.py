"""Versioned offline portfolio experiments with real cash transfers between desks.

All rules read trailing/as-of data. A governor can redeem available desk cash
and subscribe to another desk at opening NAV; it cannot teleport securities.
Historical research is retrospective even when its execution clock is causal.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .investment_replay import DESKS, ReplayData, metrics, ranks, target_weights


def choose(data, day, desk, policy, variant, units, values, nav):
    weights, score, reason = target_weights(data, day, desk, policy['max_position_weight_per_team'])
    if variant == 'baseline' or desk.startswith('baseline'):
        return weights, score, reason
    f = {k: v[day] for k, v in data.features.items()}
    eligible = f['eligible'].astype(bool)
    allowed = eligible & np.isfinite(score)
    if desk.startswith('pulse') or desk == 'adaptive_tactical':
        allowed &= f['trend'] > 0
    if variant in ('guarded', 'governed'):
        # Fixed 200-session guard is also applied to long-horizon mandates.
        allowed &= np.isfinite(f['trend200']) & (f['trend200'] > 0)
        if desk.startswith('compound') or desk == 'adaptive_quality':
            score = .75 * score + .25 * ranks(f['mom126'], eligible)
            allowed &= np.isfinite(score)
    order = np.flatnonzero(allowed)
    order = order[np.argsort(-score[order], kind='stable')]
    buffered = order[:policy['rank_buffer']]
    retained = [j for j in buffered if units[j] > 1e-9][:10]
    selected = retained + [j for j in order if j not in retained][:10-len(retained)]
    exposure = 1.
    breadth = float(np.mean(f['trend'][eligible] > 0)) if eligible.any() else 0.
    if desk.startswith('adaptive') and breadth < .45:
        exposure = .5
    if variant in ('guarded', 'governed') and selected:
        # Marginal-volatility average: conservative sizing proxy, not covariance VaR.
        vol = float(np.nanmean(f['vol63'][selected]))
        if np.isfinite(vol) and vol > 0:
            exposure = min(exposure, policy['volatility_budget'] / vol)
        if breadth < .45:
            exposure = min(exposure, .5)
    weights = np.zeros(len(score))
    if selected:
        weights[selected] = min(exposure/len(selected), policy['max_position_weight_per_team'])
    return weights, score, f'{variant}; rank_buffer={policy["rank_buffer"]}; breadth={breadth:.3f}; exposure={exposure:.4f}'


def governor_targets(books, names, policy):
    """Unitized net returns exclude capital subscriptions and redemptions."""
    scores, paused = {}, []
    for name in names:
        history = np.asarray(books[name]['unit_history'][-127:])
        if len(history) < 127:
            return {n: .3 for n in names}, {}, []
        m = metrics(history[1:], history[0])
        score = m['net_return'] + .5 * m['max_drawdown']
        scores[name] = float(score)
        if m['net_return'] < 0 and m['max_drawdown'] < -policy['probation_drawdown']:
            paused.append(name)
    ranked = sorted(names, key=lambda n: (-scores[n], n))
    weights = dict(zip(ranked, [.45, .30, .15]))
    for name in paused:
        weights[name] = 0.
    remaining = .9 - sum(weights.values())
    for name in ranked:
        if name not in paused:
            extra = min(remaining, .45 - weights[name])
            weights[name] += extra
            remaining -= extra
    return weights, scores, paused


def replay_v2(data: ReplayData, policy: dict, variant='baseline', cost_multiplier=1., fill_delay=1,
              rebalance_phase=0, record_details=True):
    if variant not in ('baseline', 'efficient', 'guarded', 'governed'):
        raise ValueError('Unknown predeclared variant')
    if fill_delay < 1:
        raise ValueError('Same-session execution is forbidden')
    initial = float(policy['initial_capital']['us'])
    rate = policy['all_in_cost_bps']['us'] * cost_multiplier / 10000
    begin = int(data.dates.searchsorted(policy['start']))
    n = len(data.tickers)
    membership = {t: c for c, cfg in policy['companies'].items() for t in cfg['teams']}
    reserves = {c: .1*initial for c in policy['companies']}
    books = {}
    for name in DESKS:
        capital = initial * (.3 if name in membership else 1.)
        books[name] = dict(units=np.zeros(n), cash=capital, fund_units=capital, pending=None,
                           nav=[], reserved_nav=[], unit_history=[], cost=0., turnover=0.,
                           stale_days=0, max_stale_weight=0., trade_count=0, flows=0.)
    last = np.full(n, np.nan)
    stale = np.zeros(n, dtype=int)
    trades, decisions, transfers, governance = [], [], [], []
    company_series = {c: [] for c in reserves}
    company_reserved = {c: [] for c in reserves}
    funding = {}  # absolute desk NAV targets fixed at the previous signal close
    unfilled = 0
    for day, date in enumerate(data.dates):
        valid_close = data.valid_close[day]
        if day < begin:
            last = np.where(valid_close, data.close[day], last)
            continue
        date_s = str(date.date())
        opening = np.where(data.valid_open[day], data.opening[day], last)
        used = {k: np.zeros(n) for k in list(reserves)+['baseline_equal_weight', 'baseline_momentum']}
        eligible_orders = {name: b for name, b in books.items() if b['pending'] is not None and b['pending']['due'] <= day}
        deltas = {}
        # Complete every sale before transferring cash and executing purchases.
        for name, b in eligible_orders.items():
            cap = data.capacity[day-1] if day else np.zeros(n)
            group = membership.get(name, name)
            cap = np.maximum(0., cap - used[group])
            valid = data.valid_open[day] & np.isfinite(cap) & (cap > 0)
            desired = b['pending']['quantity'] - b['units']
            delta = np.where(valid, np.clip(desired, -cap, cap), 0.)
            sell = np.maximum(np.minimum(delta, 0.), -b['units'])
            b['cash'] += float(np.nansum(-sell*opening))*(1-rate)
            b['units'] += sell
            used[group] += abs(sell)
            deltas[name] = [desired, sell, np.maximum(delta, 0.)]
        # Subscriptions/redemptions at opening NAV preserve per-desk unit returns.
        for company, f in list(funding.items()):
            if f['due'] > day:
                continue
            names = policy['companies'][company]['teams']
            before = reserves[company] + sum(books[k]['cash'] for k in names)
            for name in names:
                b = books[name]
                value = b['cash'] + float(np.nansum(b['units']*opening))
                take = min(b['cash'], max(0., value-f['targets'][name]))
                if take > 1e-8:
                    unit_price = value/b['fund_units'] if b['fund_units'] else 1.
                    b['fund_units'] -= take/unit_price
                    b['cash'] -= take
                    reserves[company] += take
                    transfers.append(dict(date=date_s, company=company, desk=name, amount=-take, unit_price=unit_price))
            needs = {name: max(0., f['targets'][name] - books[name]['cash'] - float(np.nansum(books[name]['units']*opening))) for name in names}
            available = max(0., reserves[company]-f['reserve_target'])
            scale = min(1., available/sum(needs.values())) if sum(needs.values()) else 0.
            for name, need in needs.items():
                add = need*scale
                if add > 1e-8:
                    b = books[name]
                    value = b['cash']+float(np.nansum(b['units']*opening))
                    unit_price = value/b['fund_units'] if b['fund_units'] > 1e-8 else (b['unit_history'][-1] if b['unit_history'] else 1.)
                    b['fund_units'] += add/unit_price
                    b['cash'] += add
                    reserves[company] -= add
                    transfers.append(dict(date=date_s, company=company, desk=name, amount=add, unit_price=unit_price))
            after = reserves[company] + sum(books[k]['cash'] for k in names)
            if not np.isclose(before, after, atol=1e-7):
                raise AssertionError('Company cash transfer conservation failed')
            del funding[company]
        for name, b in eligible_orders.items():
            desired, sell, buy = deltas[name]
            group = membership.get(name, name)
            cap = np.maximum(0., data.capacity[day-1]-used[group])
            buy = np.minimum(buy, np.where(np.isfinite(cap), cap, 0.))
            purchase = float(np.nansum(buy*opening))
            scale = min(1., max(0., b['cash'])/(purchase*(1+rate))) if purchase else 1.
            buy *= scale
            used[group] += buy
            b['cash'] -= float(np.nansum(buy*opening))*(1+rate)
            b['units'] += buy
            delta = sell+buy
            notional = abs(delta*opening)
            b['cost'] += float(np.nansum(notional))*rate
            b['turnover'] += float(np.nansum(notional))
            b['trade_count'] += int((abs(delta)>1e-9).sum())
            unfilled += int(np.any(abs(desired-delta)>1e-8))
            if record_details:
                for j in np.flatnonzero(abs(delta)>1e-9):
                    trades.append(dict(desk=name, signal_date=b['pending']['signal_date'], fill_date=date_s,
                                       ticker=data.tickers[j], signed_adjusted_units=float(delta[j]),
                                       adjusted_open=float(opening[j]), notional=float(notional[j]), cost=float(notional[j]*rate)))
            b['pending'] = None
            if b['cash'] < -1e-7 or b['units'].min() < -1e-7:
                raise AssertionError('Negative cash or position')
        last = np.where(valid_close, data.close[day], last)
        stale = np.where(valid_close, 0, stale+1)
        navs, values = {}, {}
        for name, b in books.items():
            values[name] = np.where(b['units']>0, b['units']*last, 0.)
            nav = b['cash']+float(np.nansum(values[name]))
            navs[name] = nav
            stale_value = float(np.nansum(values[name][stale>policy['max_stale_sessions']]))
            b['nav'].append(nav)
            b['reserved_nav'].append(nav-stale_value)
            b['unit_history'].append(nav/b['fund_units'] if b['fund_units']>1e-8 else (b['unit_history'][-1] if b['unit_history'] else 1.))
            if stale_value:
                b['stale_days'] += 1
                b['max_stale_weight'] = max(b['max_stale_weight'], stale_value/max(nav,1e-8))
        for company, cfg in policy['companies'].items():
            company_series[company].append(reserves[company]+sum(navs[t] for t in cfg['teams']))
            company_reserved[company].append(reserves[company]+sum(books[t]['reserved_nav'][-1] for t in cfg['teams']))
        gov_due = variant == 'governed' and day-begin >= 126 and (day-begin)%63 == 0
        if gov_due:
            for company, cfg in policy['companies'].items():
                weights, scores, paused = governor_targets(books, cfg['teams'], policy)
                total = company_series[company][-1]
                funding[company] = dict(due=day+fill_delay, targets={t: total*weights[t] for t in cfg['teams']}, reserve_target=total*(1-sum(weights.values())))
                governance.append(dict(date=date_s, company=company, lookback_sessions=126, weights=weights, scores=scores, paused=paused, action='quarterly_review; cash_only_transfers_at_next_open'))
        for name, b in books.items():
            interval = DESKS[name][0] if variant=='baseline' or name.startswith('baseline') else policy['efficient_intervals'].get(name,DESKS[name][0])
            interval = policy.get('desk_interval_overrides', {}).get(name, interval)
            if (day-begin-rebalance_phase)%interval != 0 and not (gov_due and name in membership):
                continue
            if b['pending'] is not None:
                continue
            target_nav = funding[membership[name]]['targets'][name] if gov_due and name in membership else navs[name]
            weights, score, reason = choose(data, day, name, policy, variant, b['units'], values[name], navs[name])
            quantity = np.divide(target_nav*weights, last, out=np.zeros(n), where=np.isfinite(last)&(last>0))
            if variant != 'baseline' and not name.startswith('baseline') and not gov_due:
                small = (abs(quantity-b['units'])*last < policy['no_trade_band']*navs[name]) & (quantity>0) & (b['units']>0)
                quantity = np.where(small, b['units'], quantity)
            quantity = np.where(~valid_close & (b['units']>0), b['units'], quantity)
            b['pending'] = dict(quantity=quantity, due=day+fill_delay, signal_date=date_s)
            if record_details:
                positions = []
                for j in np.flatnonzero(weights):
                    available = pd.Timestamp(data.features['filing_available_at'][day,j])
                    if pd.notna(available) and available > date:
                        raise AssertionError('Future filing in decision')
                    fid=data.features['filing_id'][day,j]
                    positions.append(dict(ticker=data.tickers[j], target_weight=round(float(weights[j]),6), score=round(float(score[j]),6), filing_available_at=None if pd.isna(available) else str(available.date()), filing_id=None if pd.isna(fid) else str(fid)))
                decisions.append(dict(desk=name, company=membership.get(name,'control'), signal_date=date_s,
                                      rule=reason, target_capital=target_nav, target_exposure=float(weights.sum()),
                                      positions=positions, pending_at_end=day+fill_delay>=len(data.dates)))
    dates = [str(d.date()) for d in data.dates[begin:]]
    series = {name:b['nav'] for name,b in books.items()} | company_series
    reserved = {name:b['reserved_nav'] for name,b in books.items()} | company_reserved
    # A funded control consists of a 90% sleeve plus a 10% cash reserve.
    for kind in ('series', 'reserved'):
        target = series if kind=='series' else reserved
        target['baseline_cash_matched'] = (.9*np.asarray(target['baseline_equal_weight'])+.1*initial).tolist()
    summary = {}
    for name, nav in series.items():
        base = initial*(.3 if name in membership else 1.)
        # A redeemed desk may have zero dollar NAV. Its return is measured per
        # fund unit; dividing dollar NAV through capital transfers is undefined.
        perf_nav = np.asarray(books[name]['unit_history']) if name in books else np.asarray(nav)
        perf_base = 1. if name in books else base
        summary[name] = metrics(perf_nav,perf_base)
        summary[name]['final_nav'] = nav[-1]
        summary[name]['return_basis'] = 'unitized_after_fund_flows' if name in books else 'company_nav'
        summary[name]['cagr'] = (perf_nav[-1]/perf_base)**(252/max(1,len(nav)))-1
        summary[name]['stale_reserved_return'] = reserved[name][-1]/base-1
        if name in books:
            b=books[name]
            summary[name]['stale_reserved_return'] = reserved[name][-1]/b['fund_units']-1 if b['fund_units']>1e-8 else b['unit_history'][-1]-1
            summary[name].update(cost_paid=b['cost'], trade_count=b['trade_count'], turnover=b['turnover']/base,
                                 stale_days=b['stale_days'], max_stale_weight=b['max_stale_weight'],
                                 unitized_return=b['unit_history'][-1]-1)
        elif name in policy['companies']:
            desks=policy['companies'][name]['teams']
            summary[name].update(cost_paid=sum(books[t]['cost'] for t in desks),trade_count=sum(books[t]['trade_count'] for t in desks),stale_days=sum(books[t]['stale_days'] for t in desks))
        else:
            b=books['baseline_equal_weight']
            summary[name].update(cost_paid=.9*b['cost'], trade_count=b['trade_count'],stale_days=b['stale_days'])
        summary[name]['periods']={}
        for label,start,end in policy['review_windows']:
            indexes=np.flatnonzero((np.asarray(dates)>=start)&(np.asarray(dates)<=end))
            if len(indexes):
                prev=perf_base if indexes[0]==0 else perf_nav[indexes[0]-1]
                summary[name]['periods'][label]=metrics(perf_nav[indexes],prev)
    return dict(variant=variant, dates=dates, series=series, reserved_series=reserved,summary=summary,
                trades=trades, decisions=decisions, governance=governance,transfers=transfers,unfilled_order_batches=unfilled,
                audit=data.audit, fill_delay=fill_delay, cost_multiplier=cost_multiplier,rebalance_phase=rebalance_phase)
