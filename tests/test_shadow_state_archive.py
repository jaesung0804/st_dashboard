import hashlib
import json
import subprocess
import sys
from pathlib import Path


def test_shadow_archive_roundtrip_keeps_every_model_and_prediction_byte(tmp_path):
    scripts = Path(__file__).resolve().parents[1] / 'scripts'
    root = tmp_path / 'data/dashboard_ews_shadow'
    expected = {}
    for i in range(150):
        file = root / 'us/ews-smooth-macro-v2/models/2026-09' / f'fixture-{i}.json'
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(f'{{"number":{i},"line":"\\n"}}\r\n'.encode())
        expected[file.relative_to(tmp_path).as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
    subprocess.run([sys.executable,str(scripts/'pack_dashboard_state.py'),'--state-dir','packed',
                    '--paths','data/dashboard_ews_shadow'],cwd=tmp_path,check=True,capture_output=True)
    manifest=json.loads((tmp_path/'packed/state-manifest.json').read_text())
    entry=manifest['data/dashboard_ews_shadow']
    assert entry['type']=='archive' and entry['files']==150
    assert len(entry['parts'])==1
    restored=tmp_path/'restored';restored.mkdir()
    subprocess.run([sys.executable,str(scripts/'unpack_dashboard_state.py'),'--state-dir',str(tmp_path/'packed')],
                   cwd=restored,check=True,capture_output=True)
    assert {p.relative_to(restored).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (restored/'data/dashboard_ews_shadow').rglob('*') if p.is_file()}==expected
