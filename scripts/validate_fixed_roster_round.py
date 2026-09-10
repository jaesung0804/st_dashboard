"""Independent arithmetic and ledger checks on the completed r03 bundle."""
from datetime import datetime,timezone
import gzip,hashlib,json
from pathlib import Path
import sys
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ai_stock_assistant.investment_replay import sha256,write_json
from ai_stock_assistant.investment_rounds import validate_roster_lock


def main():
    source=Path('docs/replays/2026-09-10-r03-fixed-roster');manifest=json.loads((source/'manifest.json').read_text())
    for name,digest in manifest['artifacts'].items():assert sha256(source/name)==digest,name
    summary=json.loads((source/'summary.json').read_text());series_count=0;decisions=0;trades=0
    protocol=json.loads((source/'protocol.json').read_text());staff={p['id'] for p in protocol['people']}
    for key,case in summary['runs'].items():
        path=source/key;nav=pd.read_csv(path/'nav.csv.gz');seen=set()
        for name,m in case['summary'].items():
            initial=30000 if name in {p['team'] for p in protocol['people']} else 100000
            curve=np.r_[initial,nav[name].to_numpy()];assert np.isfinite(curve).all()
            assert np.isclose(curve[-1]/initial-1,m['net_return'],atol=1e-9),(key,name,'return')
            assert np.isclose(np.min(curve/np.maximum.accumulate(curve)-1),m['max_drawdown'],atol=1e-9),(key,name,'drawdown')
            series_count+=1
        if not case['full_ledger_saved']:continue
        previous='0'*64
        for index,line in enumerate(gzip.open(path/'decisions.jsonl.gz','rt')):
            row=json.loads(line);digest=row.pop('hash');assert row['sequence']==index and row['previous_hash']==previous
            assert hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()==digest
            assert all(a['id'] in staff for a in row.get('employees') or [])
            assert all(not p['filing_available_at'] or p['filing_available_at']<=row['signal_date'] for p in row['positions'])
            previous=digest;decisions+=1
        assert previous==case['ledger_tail']
        frame=pd.read_csv(path/'trades.csv.gz');assert (frame.fill_date>frame.signal_date).all();trades+=len(frame)
        for team,cost in frame.groupby('desk').cost.sum().items():
            assert np.isclose(cost,case['summary'][team]['cost_paid'],atol=1e-8),(key,team,'fees')
    record=json.loads(Path('data/reference/investment_rounds.json').read_text())['rounds'][-1]
    assert validate_roster_lock(record,json.loads(Path('data/reference/investment_organization.json').read_text()))['valid']
    report=dict(checked_at=datetime.now(timezone.utc).isoformat(),result='passed',artifact_hashes=len(manifest['artifacts']),
                nav_return_drawdown_series=series_count,hash_chained_decisions=decisions,trades_with_next_session_clock=trades,
                roster_count=len(staff),no_personnel_changes=True,source_manifest_sha256=sha256(source/'manifest.json'),
                validation_script_sha256=sha256(Path(__file__)),checks=['artifact_hashes','NAV_returns_and_full_daily_drawdowns','hash_chain','filing_clock','share_trade_fee_totals','strict_next_session_fills','roster_membership'])
    output=Path('docs/validation/investment_r03_validation.json');write_json(output,report);print(json.dumps(report))


if __name__=='__main__':main()
