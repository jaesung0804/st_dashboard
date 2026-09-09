from __future__ import annotations

import base64
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest
import requests

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import push_dashboard_state as push
import recover_dashboard_state as recover
from unpack_dashboard_state import restore_split, restore_state


class FakeGitHub:
    def __init__(self, *, fail_upload=False, race=False, lost_response=False, truncated=False):
        self.head = "old"
        self.files = {"old-data.csv": "old-blob"}
        self.trees = {}
        self.events = []
        self.fail_upload, self.race = fail_upload, race
        self.lost_response, self.truncated = lost_response, truncated

    def request(self, method, endpoint, body=None):
        self.events.append((method, endpoint, body))
        if method == "GET" and endpoint == "/git/ref/heads/dashboard-state":
            return {"object": {"sha": self.head}}
        if method == "GET" and endpoint == "/git/commits/old":
            return {"tree": {"sha": "old-tree"}}
        if method == "GET" and endpoint.startswith("/git/trees/"):
            return {"truncated": self.truncated, "tree": [
                {"path": p, "sha": s, "type": "blob", "mode": "100644"} for p, s in self.files.items()]}
        if method == "POST" and endpoint == "/git/blobs":
            assert self.files == {"old-data.csv": "old-blob"}
            if self.fail_upload:
                raise RuntimeError("HTTP 408")
            if self.race:
                self.head = "concurrent"
            return {"sha": push.blob_sha(base64.b64decode(body["content"]))}
        if method == "POST" and endpoint == "/git/trees":
            assert "base_tree" not in body  # Always a complete snapshot.
            assert all("/" not in item["path"] for item in body["tree"])
            sha = f"new-tree-{len(self.trees)}"
            self.trees[sha] = body["tree"]
            return {"sha": sha}
        if method == "POST" and endpoint == "/git/commits":
            assert body["parents"] == ["old"]
            self.pending_tree = body["tree"]
            return {"sha": "new"}
        if method == "PATCH":
            assert body == {"sha": "new", "force": False}
            self.head, self.files = "new", self._flatten(self.pending_tree)
            if self.lost_response:
                raise RuntimeError("Response lost after server committed")
            return {"object": {"sha": "new"}}
        raise AssertionError((method, endpoint))

    def _flatten(self, tree, prefix=""):
        files = {}
        for item in self.trees[tree]:
            path = f"{prefix}/{item['path']}" if prefix else item["path"]
            if item["type"] == "blob":
                files[path] = item["sha"]
            else:
                files.update(self._flatten(item["sha"], path))
        return files


def snapshot(tmp_path):
    (tmp_path / "state-manifest.json").write_text("{}")
    (tmp_path / "model.txt").write_bytes(b"frozen\r\nmodel\x00")
    return tmp_path


def test_failed_blob_upload_cannot_change_live_state(tmp_path):
    api = FakeGitHub(fail_upload=True)
    with pytest.raises(RuntimeError, match="408"):
        push.publish_snapshot(snapshot(tmp_path), "old", api)
    assert api.head == "old" and api.files == {"old-data.csv": "old-blob"}
    assert not any(method == "PATCH" for method, _, _ in api.events)


@pytest.mark.parametrize("lost_response,truncated", [(False, False), (True, False), (False, True)])
def test_only_complete_verified_snapshot_is_published(tmp_path, lost_response, truncated):
    api = FakeGitHub(lost_response=lost_response, truncated=truncated)
    root = snapshot(tmp_path)
    assert push.publish_snapshot(root, "old", api) == "new"
    assert api.files == {p.name: push.blob_sha(p.read_bytes()) for p in root.iterdir()}


def test_complete_tree_is_built_bottom_up_from_direct_children(tmp_path):
    root = snapshot(tmp_path)
    nested = root / "nested/deeper/rows.json"
    nested.parent.mkdir(parents=True)
    nested.write_text("[]")
    api = FakeGitHub()
    assert push.publish_snapshot(root, "old", api) == "new"
    tree_requests = [body for method, endpoint, body in api.events
                     if method == "POST" and endpoint == "/git/trees"]
    assert len(tree_requests) == 3
    assert all("/" not in entry["path"] for body in tree_requests for entry in body["tree"])
    assert api.files["nested/deeper/rows.json"] == push.blob_sha(b"[]")


def test_concurrent_state_change_is_never_overwritten(tmp_path):
    api = FakeGitHub(race=True)
    with pytest.raises(RuntimeError, match="advanced during upload"):
        push.publish_snapshot(snapshot(tmp_path), "old", api)
    assert api.head == "concurrent"
    assert not any(method == "PATCH" for method, _, _ in api.events)


def test_oversized_blob_is_rejected_before_any_remote_write(tmp_path, monkeypatch):
    monkeypatch.setattr(push, "MAX_BLOB", 4)
    api = FakeGitHub()
    with pytest.raises(ValueError, match="repack first"):
        push.publish_snapshot(snapshot(tmp_path), "old", api)
    assert not api.events


@pytest.mark.parametrize("status,count", [(408, 3), (403, 1)])
def test_api_retries_are_bounded_and_never_echo_credentials(monkeypatch, status, count):
    api = push.GitHubAPI("owner/repo", "never-echo-this")
    calls = []
    def request(method, url, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=status, headers={})
    monkeypatch.setattr(api.session, "request", request)
    monkeypatch.setattr(push.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError) as error:
        api.request("POST", "/git/blobs", {"content": "private-data"})
    assert len(calls) == count and calls[0]["timeout"] == (10, 60)
    assert "never-echo-this" not in str(error.value) and "private-data" not in str(error.value)


def pack(cwd, state, *paths):
    subprocess.run([sys.executable, str(SCRIPTS / "pack_dashboard_state.py"),
                    "--state-dir", str(state), "--part-size", "256", "--paths", *paths],
                   cwd=cwd, check=True, capture_output=True)


def test_financial_archive_roundtrip_is_complete_deterministic_and_byte_exact(tmp_path):
    source, state, restored = (tmp_path / name for name in ("source", "state", "restored"))
    paths = ["data/raw/opendart_accounts", "data/raw/yfinance_financials"]
    expected = {}
    for folder in paths:
        for index in range(35):
            rel = f"{folder}/한글-{index}/quote.csv"
            expected[rel] = b"a,b\r\n1,2\r\r\n" + bytes([index]) * 300
            target = source / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(expected[rel])
    pack(source, state, *paths)
    before = {p.relative_to(state): p.read_bytes() for p in state.rglob("*") if p.is_file()}
    pack(source, state, *paths)
    assert before == {p.relative_to(state): p.read_bytes() for p in state.rglob("*") if p.is_file()}
    restore_state(state, restored)
    assert expected == {p.relative_to(restored).as_posix(): p.read_bytes() for p in restored.rglob("*") if p.is_file()}
    manifest = json.loads((state / "state-manifest.json").read_text())
    assert sum(info["files"] for info in manifest.values()) == 70
    assert all((state / part).stat().st_size <= 256 for info in manifest.values() for part in info["parts"])


def test_corrupt_gzip_does_not_replace_existing_price_file(tmp_path):
    part = tmp_path / "part"
    part.write_bytes(gzip.compress(b"wrong data"))
    target = tmp_path / "price.csv"
    target.write_bytes(b"keep old data")
    with pytest.raises(RuntimeError, match="SHA-256"):
        restore_split(tmp_path, target, {"parts": ["part"], "encoding": "gzip",
                                       "size": 10, "sha256": "0" * 64})
    assert target.read_bytes() == b"keep old data"


@pytest.mark.parametrize("name", ["../outside", "data/../../outside", ".git/config", "C:/outside"])
def test_recovery_rejects_archive_path_escape(tmp_path, name):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(name, b"bad")
    with pytest.raises(ValueError, match="Unsafe"):
        recover.extract_backup(archive, tmp_path / "out")
    assert not (tmp_path / "outside").exists()
