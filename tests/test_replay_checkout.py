"""Completed evidence must survive Windows-style Git checkout byte-for-byte."""
import hashlib
from pathlib import Path
import shutil
import subprocess


def test_windows_checkout_preserves_replay_manifest_bytes(tmp_path):
    root = Path(__file__).resolve().parents[1]
    shutil.copyfile(root / ".gitattributes", tmp_path / ".gitattributes")
    relative = "docs/replays/completed-run/data_provenance.json"
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    original = b'{\n  "source": "verified observation"\n}\n'
    target.write_bytes(original)
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True,
                              capture_output=True, text=True)
    git("init", "--quiet")
    git("config", "core.autocrlf", "true")
    git("add", ".gitattributes", relative)
    target.unlink()
    git("checkout-index", "--force", "--", relative)
    assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(original).digest()
