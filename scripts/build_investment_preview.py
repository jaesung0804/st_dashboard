"""Build an isolated, inspectable preview from an archived research result."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def build(source: Path, destination: Path):
    data = json.loads((source / 'investment_view.json').read_text(encoding='utf-8'))
    destination.mkdir(parents=True, exist_ok=True)
    assets = Path(__file__).parent / 'dashboard_web'
    template = (assets / 'investment.html').read_text(encoding='utf-8')
    # Inline JSON supports a local preview; escape raw-text HTML termination.
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    (destination / 'index.html').write_text(template.replace('@@DATA@@', payload), encoding='utf-8')
    for name in ('dashboard.css', 'investment.css', 'investment.js'):
        (destination / name).write_bytes((assets / name).read_bytes())


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--destination', type=Path, required=True)
    a = p.parse_args()
    build(a.source, a.destination)
