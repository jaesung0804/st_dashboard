"""Read-only, bounded inventory of existing investment inputs in pipeline-state."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from research_backend_client import Client, safe_path
from unpack_dashboard_state import restore_state


def main():
    root = Path('.research-backend/artifacts/investment-input-inventory')
    client = Client(project='investment')
    sid = client.json('GET', '/snapshot-heads/pipeline-state')['snapshot_id']
    entries = {e['relative_path']: e for e in client.snapshot_entries(sid)}
    packed = root / 'packed'

    def download(path):
        e = entries[path]
        client.download(e['sha256'], safe_path(packed, path), e['byte_size'])

    download('state-manifest.json')
    manifest = json.loads((packed / 'state-manifest.json').read_text())
    candidates = {p: info for p, info in manifest.items()
                  if ('filing_events' in p or 'macro_indicators_long' in p or 'fx' in p.lower()
                      or p in ('data/dashboard_research', 'data/dashboard_ews_shadow'))}
    selected = candidates
    needed = {p for p, info in selected.items() if info['type'] == 'file'}
    needed.update(part for info in selected.values() for part in info.get('parts', []))
    if sum(entries[p]['byte_size'] for p in needed) > 500_000_000:
        raise ValueError('Inventory download exceeds bounded size')
    for path in sorted(needed):
        download(path)
    (packed / 'state-manifest.json').write_text(json.dumps(selected))
    restored = root / 'restored'
    restore_state(packed, restored)
    report = {'snapshot_id': sid, 'candidates': list(candidates), 'files': {}}
    paths = [p.relative_to(restored).as_posix() for p in restored.rglob('*')
             if p.is_file() and ('filing_events.csv' in p.name or 'macro_indicators_long' in p.name)]
    report['related_files'] = [p.relative_to(restored).as_posix() for p in restored.rglob('*')
                             if p.is_file() and ('fx' in p.name.lower() or 'macro' in p.name.lower())][:60]
    for path in paths:
        df = pd.read_csv(restored / path, dtype={'ticker': str, 'filing_id': str})
        summary = {'rows': len(df), 'columns': list(df)}
        if 'ticker' in df:
            summary['tickers'] = int(df.ticker.nunique())
        for col in ('available_at', 'filed', 'period_end', 'date'):
            if col in df:
                summary[col] = {'min': str(df[col].min()), 'max': str(df[col].max())}
        if 'series' in df:
            fx = df.loc[df.series.eq('usd_krw')]
            summary['usd_krw'] = {'rows': len(fx), 'first': str(fx.date.min()), 'last': str(fx.date.max())}
        report['files'][path] = summary
    (root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
