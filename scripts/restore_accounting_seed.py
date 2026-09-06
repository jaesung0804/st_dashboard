"""Restore verified filing events when a runner cannot reach official servers.

This is a dated research snapshot, never a claim that a live fetch succeeded.
"""
import gzip, hashlib, io, json, tarfile
from pathlib import Path


def main():
    root = Path('data/reference/accounting_seed')
    manifest = json.loads((root / 'manifest.json').read_text())
    payload = bytearray()
    for item in manifest['parts']:
        part = (root / item['name']).read_bytes()
        if hashlib.sha256(part).hexdigest() != item['sha256']:
            raise ValueError('Accounting snapshot part checksum mismatch')
        payload.extend(part)
    if hashlib.sha256(payload).hexdigest() != manifest['sha256']:
        raise ValueError('Accounting snapshot checksum mismatch')
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(payload))) as archive:
        expected = manifest['files']
        for member in archive.getmembers():
            if not member.isfile() or member.name not in expected:
                raise ValueError('Unexpected accounting snapshot member')
            path = Path(member.name)
            if path.is_absolute() or '..' in path.parts or path.parts[:3] != ('data', 'dashboard_research', 'accounting'):
                raise ValueError('Unsafe accounting snapshot path')
            body = archive.extractfile(member).read()
            if hashlib.sha256(body).hexdigest() != expected[member.name]:
                raise ValueError('Accounting event checksum mismatch')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
    print('Restored receipt-dated snapshot collected', manifest['collected_at'])
    print('No live official-source refresh is claimed by this step.')


if __name__ == '__main__':
    main()
