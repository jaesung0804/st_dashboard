"""Exercise the real Pages publisher against disposable local Git repositories.

No network, GitHub token, repository checkout, or shared Git index is used.
Run: python scripts/test_pages_publish_local.py
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from build_pages_deploy import DASHBOARDS, push_pages


REAL_RUN = subprocess.run


def git(*args: str, cwd: Path | None = None) -> str:
    return REAL_RUN(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def identity(root: Path) -> None:
    git("config", "user.name", "Local publisher test", cwd=root)
    git("config", "user.email", "local-test@example.invalid", cwd=root)


def remote_text(remote: Path, name: str) -> str:
    return git("--git-dir", str(remote), "show", f"gh-pages:{name}")


def head(remote: Path) -> str:
    return git("--git-dir", str(remote), "rev-parse", "gh-pages")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="pages-publisher-test-") as temporary:
        root = Path(temporary)
        remote, seed, deploy = root / "remote.git", root / "seed", root / "deploy"
        git("init", "--bare", str(remote))
        git("init", "-b", "gh-pages", str(seed))
        identity(seed)
        kr, us = DASHBOARDS["kr"]["target"], DASHBOARDS["us"]["target"]
        for name, text in {
            "index.html": "old home",
            "research/index.html": "other team research",
            "simulation/history/v1.json": '{"version":1}',
            "other-team/notes.txt": "keep this sibling",
            f"{kr}/obsolete.json": '{"window":"old"}',
            f"{us}/manifest.json": '{"latest":"2026-09-09"}',
        }.items():
            write(seed, name, text)
        git("add", ".", cwd=seed)
        git("commit", "-m", "Existing Pages history", cwd=seed)
        old_head = git("rev-parse", "HEAD", cwd=seed)
        git("remote", "add", "origin", remote.as_uri(), cwd=seed)
        git("push", "origin", "HEAD:gh-pages", cwd=seed)
        write(deploy, "index.html", "new home")
        write(deploy, "simulation/index.html", "new simulation")
        write(deploy, f"{kr}/manifest.json", '{"latest":"2026-09-10"}')
        write(deploy, f"{kr}/dates/new.json", '{"new":true}')

        push_pages(deploy, remote.as_uri())
        published = head(remote)
        assert published != old_head
        git("--git-dir", str(remote), "merge-base", "--is-ancestor", old_head, published)
        assert remote_text(remote, "research/index.html") == "other team research"
        assert remote_text(remote, "other-team/notes.txt") == "keep this sibling"
        assert remote_text(remote, "simulation/history/v1.json") == '{"version":1}'
        assert remote_text(remote, "simulation/index.html") == "new simulation"
        assert remote_text(remote, f"{us}/manifest.json") == '{"latest":"2026-09-09"}'
        assert remote_text(remote, f"{kr}/dates/new.json") == '{"new":true}'
        paths = git("--git-dir", str(remote), "ls-tree", "-r", "--name-only", "gh-pages").splitlines()
        assert f"{kr}/obsolete.json" not in paths, "Rebuilt market windows must replace their old files"
        print("PASS history, research/simulation siblings, untouched market, bounded rebuilt market")

        push_pages(deploy, remote.as_uri())
        assert head(remote) == published, "Identical bundles must not create repeated commits"
        print("PASS unchanged publication is idempotent")

        write(deploy, "simulation/race-candidate.txt", "must not appear after rejected push")
        concurrent_head: list[str] = []

        def race_run(args, *positional, **kwargs):
            if isinstance(args, list) and args[-3:] == ["push", "origin", "HEAD:gh-pages"]:
                other = root / "concurrent-writer"
                git("clone", "--branch", "gh-pages", remote.as_uri(), str(other))
                identity(other)
                write(other, "research/index.html", "concurrent research update")
                git("add", ".", cwd=other)
                git("commit", "-m", "Concurrent research publication", cwd=other)
                git("push", "origin", "HEAD:gh-pages", cwd=other)
                concurrent_head.append(git("rev-parse", "HEAD", cwd=other))
            return REAL_RUN(args, *positional, **kwargs)

        with patch("build_pages_deploy.subprocess.run", side_effect=race_run):
            try:
                push_pages(deploy, remote.as_uri())
            except subprocess.CalledProcessError as exc:
                assert exc.cmd[-3:] == ["push", "origin", "HEAD:gh-pages"]
            else:
                raise AssertionError("A concurrent remote update must reject the stale publisher")
        assert len(concurrent_head) == 1 and head(remote) == concurrent_head[0]
        assert remote_text(remote, "research/index.html") == "concurrent research update"
        final_paths = git("--git-dir", str(remote), "ls-tree", "-r", "--name-only", "gh-pages").splitlines()
        assert "simulation/race-candidate.txt" not in final_paths
        git("--git-dir", str(remote), "merge-base", "--is-ancestor", published, head(remote))
        print("PASS concurrent push rejected without losing the other team's update or history")
        print("3 local Pages publication checks passed")


if __name__ == "__main__":
    main()
