"""Build an isolated, inspectable preview from an archived research result."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path


def build(source: Path, destination: Path):
    data = json.loads((source / 'investment_view.json').read_text(encoding='utf-8'))
    destination.mkdir(parents=True, exist_ok=True)
    # Keep the complete public evidence available, but avoid making a phone
    # parse every filing and chart before it can show the first 20 cards.
    (destination / 'research.json').write_bytes((source / 'investment_view.json').read_bytes())
    display = copy.deepcopy(data)
    originals = data.get('rows', [])
    chunks = destination / 'details'
    if originals:
        chunks.mkdir(exist_ok=True)
    for start in range(0, len(originals), 128):
        filename = f'{start//128:03}.json'
        chunk_bytes = json.dumps(originals[start:start+128], ensure_ascii=False,
            allow_nan=False, separators=(',', ':')).encode('utf-8')
        (chunks / filename).write_bytes(chunk_bytes)
        chunk_hash = hashlib.sha256(chunk_bytes).hexdigest()[:12]
        for row in display['rows'][start:start+128]:
            row['detail_bundle'] = 'details/' + filename + '?v=' + chunk_hash
            row['opinion'] = {k: row['opinion'][k] for k in ('status', 'label')}
            if row.get('financials'):
                row['financials'].pop('history', None)
            row['scenarios'] = {k: {'score': v['score']} for k, v in row.get('scenarios', {}).items()}
            prices = row.get('price_metrics')
            if prices and len(prices.get('chart', [])) > 16:
                chart = prices['chart']
                prices['chart'] = [chart[round(i*(len(chart)-1)/15)] for i in range(16)]
    display['full_report'] = 'research.json'
    assets = Path(__file__).parent / 'dashboard_web'
    template = (assets / 'investment.html').read_text(encoding='utf-8')
    for name in ('dashboard.css', 'investment.css', 'investment-metrics.js', 'investment.js'):
        digest = hashlib.sha256((assets / name).read_bytes()).hexdigest()[:12]
        template = template.replace('"' + name + '"', '"' + name + '?v=' + digest + '"')
    report_hash = hashlib.sha256((source / 'investment_view.json').read_bytes()).hexdigest()[:12]
    template = template.replace('href="research.json"', f'href="research.json?v={report_hash}"')
    # Inline JSON supports a local preview; escape raw-text HTML termination.
    payload = json.dumps(display, ensure_ascii=False, allow_nan=False, separators=(',', ':')).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    (destination / 'index.html').write_text(template.replace('@@DATA@@', payload), encoding='utf-8')
    for name in ('dashboard.css', 'investment.css', 'investment-metrics.js', 'investment.js'):
        (destination / name).write_bytes((assets / name).read_bytes())


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--destination', type=Path, required=True)
    a = p.parse_args()
    build(a.source, a.destination)
