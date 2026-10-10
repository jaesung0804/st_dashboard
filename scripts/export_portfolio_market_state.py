"""Read four canonical market files from one immutable snapshot; never update it.

The export is a retained research input artifact, not a public dashboard or a
claim of point-in-time availability. Existing pack/unpack verification is reused.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import os
import re
import tarfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from research_backend_client import Client, safe_path
from unpack_dashboard_state import restore_state, verify_file

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    'data/raw/krx_ohlcv_kospi_kosdaq_state.csv',
    'data/raw/us_ohlcv_nasdaq_nyse_yfinfo_state.csv',
    'data/raw/krx_listings_kospi_kosdaq_state.csv',
    'data/raw/us_listings_nasdaq_nyse_yfinfo_state.csv',
)
MAX_PACKED = 1024**3
MAX_RESTORED = 4 * 1024**3


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)+'\n', encoding='utf-8')


class ReadOnlyClient(Client):
    def request(self, method, path, body=None, file=None):
        if method != 'GET' or body is not None or file is not None:
            raise ValueError('This export permits read-only backend access')
        return super().request(method, path)


def selected_sources(manifest, entries):
    if not all(p in manifest for p in FILES):
        raise ValueError('Incomplete canonical market inputs')
    selected = {p: manifest[p] for p in FILES}
    needed = set()
    for rel, info in selected.items():
        if (info.get('type') not in {'file','split'} or type(info.get('size')) is not int
                or info['size'] <= 0 or not re.fullmatch('[0-9a-f]{64}', str(info.get('sha256','')))):
            raise ValueError('Market source requires a checked size and hash')
        if info['type'] == 'file':
            needed.add(rel)
        else:
            parts = info.get('parts', [])
            if (not isinstance(parts,list) or not 1 <= len(parts) <= 512
                    or parts != [f'.parts/{rel}/part-{i:04d}' for i in range(len(parts))]
                    or info.get('encoding') not in {None,'raw','gzip'}):
                raise ValueError('Invalid market split file')
            needed.update(parts)
    if sum(info['size'] for info in selected.values()) > MAX_RESTORED:
        raise ValueError('Restored market export exceeds its size budget')
    for rel in needed:
        entry = entries.get(rel,{})
        if (not re.fullmatch('[0-9a-f]{64}', str(entry.get('sha256','')))
                or type(entry.get('byte_size')) is not int or entry['byte_size'] <= 0):
            raise ValueError('Missing or invalid snapshot file')
    if sum(entries[p]['byte_size'] for p in needed) > MAX_PACKED:
        raise ValueError('Market download exceeds its size budget')
    return selected, sorted(needed)


def price_coverage(path):
    latest = {}; rows = 0; earliest = None; newest = None
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        required = {'date','ticker','open','high','low','close','adjusted_close','volume'}
        if not required <= set(reader.fieldnames or []):
            raise ValueError('Missing canonical price columns')
        for row in reader:
            stamp = date.fromisoformat(row['date']).isoformat()
            ticker = row['ticker'].strip()
            if not ticker:raise ValueError('Empty price ticker')
            rows += 1
            earliest = min(earliest or stamp, stamp); newest = max(newest or stamp, stamp)
            latest[ticker] = max(latest.get(ticker,stamp),stamp)
    if not rows:raise ValueError('Empty price source')
    return dict(rows=rows,tickers=len(latest),first_date=earliest,last_date=newest,
        latest_date_counts=dict(sorted(Counter(latest.values()).items())),
        availability='retained_snapshot_retrieved_now_not_historical_receipt_timestamps')


def export(root, client):
    root = Path(root).resolve()
    root.relative_to((ROOT/'.research-backend/artifacts').resolve())
    if root == (ROOT/'.research-backend/artifacts').resolve():
        raise ValueError('Use a new named output directory')
    root.mkdir(parents=True,exist_ok=False)
    head = client.json('GET','/snapshot-heads/pipeline-state')
    sid = head['snapshot_id']
    if not re.fullmatch('[A-Za-z0-9-]{1,100}', str(sid)):
        raise ValueError('Invalid source snapshot identity')
    entries = {}
    for entry in client.snapshot_entries(sid):
        name = entry['relative_path']
        if name in entries or len(entries) >= 20000:
            raise ValueError('Duplicate or excessive snapshot entries')
        entries[name] = entry
    info = entries['state-manifest.json']
    if (type(info['byte_size']) is not int or not 0 < info['byte_size'] <= 4*1024**2
            or not re.fullmatch('[0-9a-f]{64}', str(info['sha256']))):
        raise ValueError('Invalid canonical state manifest')
    original = root/'source-state-manifest.json'
    client.download(info['sha256'],original,info['byte_size'])
    selected, needed = selected_sources(json.loads(original.read_text(encoding='utf-8')), entries)
    packed = root/'packed';packed.mkdir()
    for rel in needed:
        entry = entries[rel]
        client.download(entry['sha256'],safe_path(packed,rel),entry['byte_size'])
    write(packed/'state-manifest.json',selected)
    restored = root/'restored';restore_state(packed,restored)
    for rel,item in selected.items():verify_file(restored/rel,item)
    report = dict(schema_version=1,classification='canonical_market_input_export_not_live_model',
        source_snapshot_id=sid,source_manifest_sha256=info['sha256'],
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        files={p:dict(sha256=selected[p]['sha256'],size=selected[p]['size']) for p in FILES},
        packed_source_files={p:dict(sha256=entries[p]['sha256'],size=entries[p]['byte_size']) for p in needed},
        coverage={market:price_coverage(restored/rel) for market,rel in zip(('kr','us'),FILES[:2])},
        current_head_snapshot_id=client.json('GET','/snapshot-heads/pipeline-state')['snapshot_id'],
        backend_mutations=0,collection_started=False,training_started=False,production_allowed=False)
    write(root/'export.json',report)
    bundle = root/'market-inputs.tar.gz'
    with tarfile.open(bundle,'w:gz',compresslevel=3) as archive:
        for rel in FILES:archive.add(restored/rel,arcname=rel,recursive=False)
        archive.add(root/'export.json',arcname='export.json',recursive=False)
    with tarfile.open(bundle,'r:gz') as archive:
        expected={**{p:report['files'][p]['sha256'] for p in FILES},'export.json':digest(root/'export.json')}
        members=archive.getmembers()
        if len(members)!=len(expected):raise ValueError('Unexpected export member count')
        for member in members:
            if not member.isfile() or member.name not in expected:
                raise ValueError('Unexpected export member')
            if hashlib.file_digest(archive.extractfile(member),'sha256').hexdigest()!=expected[member.name]:
                raise ValueError('Export archive integrity failure')
    write(root/'receipt.json',dict(bundle_sha256=digest(bundle),bundle_size=bundle.stat().st_size,
        source_snapshot_id=sid,export_sha256=digest(root/'export.json'),production_allowed=False))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        if os.getenv('RESEARCH_STORAGE') != 'backend':
            raise ValueError('This command requires the existing configured backend')
        report=export(args.output,ReadOnlyClient(project='investment'))
        print(json.dumps({k:report[k] for k in ['source_snapshot_id','coverage','backend_mutations','production_allowed']}))
    except Exception:
        raise SystemExit('Market-state export failed; source details and credentials are not logged') from None


if __name__=='__main__':main()
