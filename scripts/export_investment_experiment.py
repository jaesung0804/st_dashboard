"""Publish only a checksum-pinned public research summary; never fit or infer."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from build_investment_preview import build
from research_backend_client import Client


def export(deploy_dir, reference=Path('data/reference/relative_ews_release.json'),
           cache=Path('.research-backend/artifacts/public-investment')):
    if not reference.exists():
        return False
    ref = json.loads(reference.read_text(encoding='utf-8'))
    if not re.fullmatch('[a-f0-9]{32}', ref['snapshot_id']) or not re.fullmatch('[a-f0-9]{64}', ref['report_sha256']):
        raise ValueError('Invalid pinned experiment reference')
    root = cache / ref['snapshot_id']
    report = root / 'investment_view.json'
    if os.getenv('RESEARCH_STORAGE') == 'backend':
        client = Client(project='investment')
        entries = [e for e in client.snapshot_entries(ref['snapshot_id']) if e['relative_path'] == 'investment_view.json']
        if len(entries) != 1 or entries[0]['sha256'] != ref['report_sha256']:
            raise ValueError('Pinned public experiment hash mismatch')
        if entries[0]['byte_size'] > 32 * 1024 * 1024:
            raise ValueError('Public summary exceeds bounded download size')
        client.download(ref['report_sha256'], report, entries[0]['byte_size'])
    elif not report.exists():
        print('No local copy of pinned experimental summary; keep the existing public experimental page.')
        return False
    import hashlib
    if hashlib.sha256(report.read_bytes()).hexdigest() != ref['report_sha256']:
        raise ValueError('Cached public experiment failed integrity check')
    presentation = None
    if os.getenv('RESEARCH_STORAGE') == 'backend':
        from export_financial_metrics import load_metrics, overlay
        metrics = load_metrics(client, cache / 'financial-metrics')
        if metrics:
            presentation = overlay(json.loads(report.read_text(encoding='utf-8')), metrics)
    build(root, Path(deploy_dir) / 'investment', presentation=presentation)
    print('Built explicitly dated investment experiment; no training or inference was run.')
    return True
