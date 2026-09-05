"""Persist state before Pages publishing. A failed push must fail the workflow."""
import argparse
import subprocess
import time
from pathlib import Path


def push_state(root: Path) -> None:
    def git(*args: str, check: bool = True):
        return subprocess.run(["git", *args], cwd=root, check=check)

    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git("config", "http.version", "HTTP/1.1")
    git("add", "-A")
    diff = git("diff", "--cached", "--quiet", check=False)
    if diff.returncode == 1:
        git("commit", "-m", "Persist monthly models and immutable daily dashboard state")
    elif diff.returncode != 0:
        raise RuntimeError("Cannot inspect staged dashboard state")
    for attempt in range(3):
        if git("push", "origin", "HEAD:dashboard-state", check=False).returncode == 0:
            return
        if attempt < 2:
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("Dashboard-state push failed. Pages was not published; recover from this run's artifact. Do not force-push.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state-dir", type=Path, default=Path(".dashboard-state"))
    push_state(p.parse_args().state_dir)
