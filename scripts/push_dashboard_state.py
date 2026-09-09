"""Upload bounded Git blobs, then atomically advance dashboard-state without force.

An interrupted blob upload cannot expose an incomplete state. A concurrent branch
update is refused; a lost final response is reconciled against the remote SHA.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import os
from pathlib import Path
import re
import subprocess
import time

import requests

MAX_BLOB = 8 * 1024 * 1024
BRANCH = "dashboard-state"


class GitHubAPI:
    def __init__(self, repository: str, token: str):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository) or not token:
            raise ValueError("A repository and GitHub token are required")
        self.base = f"https://api.github.com/repos/{repository}"
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}",
                                     "Accept": "application/vnd.github+json",
                                     "X-GitHub-Api-Version": "2022-11-28"})
        self.next_write = 0.0

    def request(self, method: str, endpoint: str, body: dict | None = None) -> dict:
        for attempt in range(3):
            if method != "GET":
                time.sleep(max(0, self.next_write - time.monotonic()))
                self.next_write = time.monotonic() + 1.0
            try:
                response = self.session.request(method, self.base + endpoint, json=body,
                                                timeout=(10, 60), allow_redirects=False)
            except requests.RequestException:
                if attempt == 2:
                    raise RuntimeError(f"GitHub {method} {endpoint}: network timeout/failure") from None
                time.sleep(2 ** attempt)
                continue
            if 200 <= response.status_code < 300:
                return response.json()
            retryable = response.status_code in (408, 429, 500, 502, 503, 504)
            retryable |= response.status_code == 403 and "Retry-After" in response.headers
            delay = max(2 ** attempt, float(response.headers.get("Retry-After", 0)))
            if not retryable or attempt == 2 or delay > 60:
                # Never log a token, a response body or the uploaded contents.
                if response.status_code == 422 and endpoint == "/git/trees":
                    # Tree validation metadata contains field/code identifiers,
                    # not blob contents. Keep enough information to diagnose it.
                    try:
                        details = response.json()
                        errors = [{k: e[k] for k in ("resource", "field", "code") if k in e}
                                  for e in details.get("errors", []) if isinstance(e, dict)]
                        raise RuntimeError(f"GitHub tree validation failed: {str(details.get('message', ''))[:250]}; {errors}")
                    except (ValueError, TypeError):
                        pass
                raise RuntimeError(f"GitHub {method} {endpoint}: HTTP {response.status_code}")
            time.sleep(delay)
        raise AssertionError("unreachable")


def blob_sha(payload: bytes) -> str:
    return hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()


def inventory(root: Path) -> dict[str, tuple[str, int]]:
    files = {}
    for directory, subdirs, names in os.walk(root):
        subdirs[:] = sorted(d for d in subdirs if d != ".git")
        for name in sorted(names):
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            if rel == ".git":
                continue
            if path.is_symlink() or path.stat().st_size > MAX_BLOB:
                raise ValueError(f"State file is not a regular bounded blob: {rel}; repack first")
            payload = path.read_bytes()
            files[rel] = (blob_sha(payload), len(payload))
    if "state-manifest.json" not in files:
        raise ValueError("Refusing to push state without its manifest")
    return files


def create_complete_tree(files: dict[str, tuple[str, int]], api: GitHubAPI) -> str:
    """Create a complete snapshot as bounded, bottom-up directory trees.

    Sending every slash-delimited path in one GitHub tree request can time out
    while GitHub expands the hierarchy, even when each blob is small. Building
    each directory independently keeps every request bounded while the final
    root tree still contains no entries inherited from the previous snapshot.
    """
    hierarchy: dict[str, object] = {}
    for rel, (sha, _) in sorted(files.items()):
        parts = rel.split("/")
        node = hierarchy
        for directory in parts[:-1]:
            child = node.setdefault(directory, {})
            if not isinstance(child, dict):
                raise ValueError(f"State path conflicts with a file: {rel}")
            node = child
        if parts[-1] in node:
            raise ValueError(f"Duplicate state path: {rel}")
        node[parts[-1]] = sha

    def create(node: dict[str, object]) -> str:
        entries = []
        for name, value in sorted(node.items()):
            if isinstance(value, dict):
                entries.append({"path": name, "mode": "040000", "type": "tree", "sha": create(value)})
            else:
                entries.append({"path": name, "mode": "100644", "type": "blob", "sha": value})
        return api.request("POST", "/git/trees", {"tree": entries})["sha"]

    return create(hierarchy)


def publish_snapshot(root: Path, base: str, api: GitHubAPI) -> str:
    files = inventory(root)
    ref = f"/git/ref/heads/{BRANCH}"
    if api.request("GET", ref)["object"]["sha"] != base:
        raise RuntimeError("Dashboard-state advanced since checkout; restore the newer state before retrying")
    base_tree = api.request("GET", f"/git/commits/{base}")["tree"]["sha"]
    old_tree = api.request("GET", f"/git/trees/{base_tree}?recursive=1")
    # The old 40,000-file tree may be truncated. It is only a deduplication hint;
    # the new tree below is a complete snapshot, never a partial patch/deletion.
    old = {item["path"]: item for item in old_tree["tree"] if item["type"] == "blob"}
    known = {item["sha"] for item in old.values()}
    uploaded = 0
    for rel, (sha, size) in sorted(files.items()):
        if sha not in known:
            payload = (root / rel).read_bytes()
            if blob_sha(payload) != sha:
                raise RuntimeError(f"Local state changed during upload: {rel}")
            result = api.request("POST", "/git/blobs", {
                "content": base64.b64encode(payload).decode("ascii"), "encoding": "base64"})
            if result["sha"] != sha:
                raise RuntimeError(f"Uploaded blob checksum mismatch: {rel}")
            known.add(sha)
            uploaded += 1
            if uploaded % 10 == 0:
                print(f"State upload: {uploaded} verified blobs, current={size / 1024 / 1024:.1f} MiB", flush=True)
    tree = create_complete_tree(files, api)
    if tree == base_tree:
        if api.request("GET", ref)["object"]["sha"] != base:
            raise RuntimeError("Dashboard-state advanced during validation")
        print(f"Dashboard-state unchanged: {base}", flush=True)
        return base
    commit = api.request("POST", "/git/commits", {
        "message": "Persist verified compressed dashboard state",
        "tree": tree, "parents": [base]})["sha"]
    # No branch ref has been modified yet. Do not publish on a stale base.
    if api.request("GET", ref)["object"]["sha"] != base:
        raise RuntimeError("Dashboard-state advanced during upload; uploaded blobs are safe to reuse on retry")
    try:
        api.request("PATCH", f"/git/refs/heads/{BRANCH}", {"sha": commit, "force": False})
    except RuntimeError:
        if api.request("GET", ref)["object"]["sha"] != commit:
            raise
    if api.request("GET", ref)["object"]["sha"] != commit:
        raise RuntimeError("Remote state SHA could not be verified; do not publish Pages")
    print(f"Dashboard-state verified: {commit} ({uploaded} uploaded blobs)", flush=True)
    return commit


def push_state(root: Path) -> str:
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()
    api = GitHubAPI(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_TOKEN", ""))
    return publish_snapshot(root, base, api)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state-dir", type=Path, default=Path(".dashboard-state"))
    push_state(p.parse_args().state_dir)
