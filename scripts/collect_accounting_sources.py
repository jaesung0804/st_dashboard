"""Cache official filing-level accounting inputs for a fixed, non-return-selected cohort."""
from __future__ import annotations
import argparse, concurrent.futures, gzip, hashlib, json, os, sys, time, urllib.request
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

ROOT=Path('data/dashboard_research/accounting_sources')
UA='Jaesung research https://github.com/jaesung0804/st_dashboard'

def get(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Encoding':'identity'}),timeout=25) as r:
                return r.read()
        except Exception:
            if attempt==2:raise
            time.sleep(1+attempt)

def collect_us(tickers):
    folder=ROOT/'us';folder.mkdir(parents=True,exist_ok=True)
    mapping=json.loads(get('https://www.sec.gov/files/company_tickers.json'))
    lookup={r['ticker'].replace('-','.'):r['cik_str'] for r in mapping.values()}
    outcome=[]
    # Three workers and a pause per request remain below the SEC rate ceiling.
    def one(ticker):
        cik=lookup.get(ticker.replace('-','.'));path=folder/(ticker+'.json.gz')
        record={'ticker':ticker,'cik':cik}
        try:
            if not cik:raise ValueError('No SEC identifier')
            if not path.exists():
                body=get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json')
                data=json.loads(body)
                if int(data['cik'])!=cik:raise ValueError('Identifier mismatch')
                path.write_bytes(gzip.compress(body,mtime=0))
            body=gzip.decompress(path.read_bytes());record.update(status='ok',sha256=hashlib.sha256(body).hexdigest())
        except Exception as error:record.update(status='failed',error=type(error).__name__)
        time.sleep(.4)
        return record
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for n,record in enumerate(pool.map(one,tickers),1):
            outcome.append(record)
            if n%16==0:print('SEC checked',n,'/',len(tickers),flush=True)
    return outcome

def collect_kr(tickers):
    from ai_stock_assistant.data.opendart import fetch_financial_statement_with_fallback,get_api_key
    import requests
    key=get_api_key();folder=ROOT/'kr';folder.mkdir(parents=True,exist_ok=True)
    codes=pd.read_csv('data/raw/opendart_corp_codes.csv',dtype=str).set_index('ticker').corp_code.to_dict()
    profiles=[];tasks=[]
    for ticker in tickers:
        corp=codes.get(ticker)
        if not corp:profiles.append({'ticker':ticker,'status':'missing_identifier'});continue
        path=folder/(ticker+'-profile.json')
        if path.exists():info=json.loads(path.read_text())
        else:
            try:
                response=requests.get('https://opendart.fss.or.kr/api/company.json',params={'crtfc_key':key,'corp_code':corp},timeout=25)
                response.raise_for_status();info=response.json()
                if info.get('status')!='000':raise ValueError('Profile unavailable')
                # Save only fields used to establish identity/fiscal calendar.
                info={k:info.get(k) for k in ['corp_code','stock_code','corp_name','acc_mt','induty_code']}
                path.write_text(json.dumps(info,ensure_ascii=False))
            except Exception as e:profiles.append({'ticker':ticker,'status':'profile_failed','error':type(e).__name__});continue
        if str(info.get('acc_mt')).zfill(2)!='12':profiles.append({'ticker':ticker,'status':'non_december_fiscal_year'});continue
        profiles.append({'ticker':ticker,'status':'ok','fiscal_month':'12'})
        for year in range(2021,2027):
            for report in ['11013','11012','11014','11011']:
                if year==2026 and report in ['11014','11011']:continue
                tasks.append((ticker,corp,year,report))
    def one(args):
        ticker,corp,year,report=args;path=folder/f'{ticker}_{year}_{report}.csv.gz'
        record={'ticker':ticker,'year':year,'report':report}
        if path.exists():return {**record,'status':'cached'}
        try:
            # The retained 2025-26 cache already contains original receipt IDs.
            old=Path('data/raw/opendart_accounts/annual_q1_half_q3_2025_2026')/f'{ticker}_{year}_{report}.csv'
            frame=pd.read_csv(old,dtype=str) if old.exists() and old.stat().st_size>100 else pd.DataFrame()
            if frame.empty:
                frame,scope,status,_=fetch_financial_statement_with_fallback(corp,year,report,key)
                if status!='000' or frame.empty:return {**record,'status':'unavailable','api_status':status}
                frame['ticker']=ticker;frame['fs_div']=scope
            if 'rcept_no' not in frame or frame.rcept_no.isna().any():raise ValueError('Receipt ID is missing')
            body=frame.to_csv(index=False).encode();path.write_bytes(gzip.compress(body,mtime=0))
            return {**record,'status':'ok','rows':len(frame),'sha256':hashlib.sha256(body).hexdigest()}
        except Exception as error:return {**record,'status':'failed','error':type(error).__name__}
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for n,result in enumerate(pool.map(one,tasks),1):
            results.append(result)
            if n%100==0:print('DART checked',n,'/',len(tasks),flush=True)
    return {'profiles':profiles,'statements':results}

def main():
    p=argparse.ArgumentParser(__doc__);p.add_argument('--market',choices=['kr','us'],required=True);a=p.parse_args()
    cohort=json.loads(Path('data/reference/accounting_cohort.json').read_text())
    ROOT.mkdir(parents=True,exist_ok=True)
    result=(collect_kr if a.market=='kr' else collect_us)(cohort['markets'][a.market])
    (ROOT/(a.market+'-collection.json')).write_text(json.dumps({'market':a.market,'generated_at':pd.Timestamp.now(tz='UTC').isoformat(),'cohort':cohort,'result':result},ensure_ascii=False,indent=2))
    print('SOURCE COLLECTION FINISHED',a.market,flush=True)

if __name__=='__main__':main()
