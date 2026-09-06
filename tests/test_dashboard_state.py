from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class DashboardStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.target = self.root / "restored"
        self.target.mkdir()
        self.rel = "data/raw/state.csv"

    def script(self, name: str, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / name), *args],
            cwd=cwd or self.target, capture_output=True, text=True,
        )

    def seed(self, chunks: list[bytes], size: int, digest: str | None = None) -> None:
        parts = []
        for index, chunk in enumerate(chunks):
            rel = f".parts/{self.rel}/part-{index:04d}"
            path = self.state / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(chunk)
            parts.append(rel)
        info = {"type": "split", "size": size, "parts": parts}
        if digest is not None:
            info["sha256"] = digest
        (self.state / "state-manifest.json").write_text(json.dumps({self.rel: info}))

    def restore(self, state: Path | None = None) -> subprocess.CompletedProcess:
        return self.script("unpack_dashboard_state.py", "--state-dir", str(state or self.state))

    def assert_refused(self, message: str) -> None:
        output = self.target / self.rel
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"keep existing data")
        result = self.restore()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stderr)
        self.assertEqual(output.read_bytes(), b"keep existing data")
        self.assertEqual(list(output.parent.glob("*.tmp")), [])

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd or self.state, check=True,
            capture_output=True, text=True,
        ).stdout

    def commit_state(self) -> None:
        self.git("init")
        self.git("config", "user.name", "test")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "core.autocrlf", "true")
        self.git("add", "-A")
        self.git("commit", "-m", "state fixture")

    def test_legacy_boundary_repair_is_exact_and_repeatable(self) -> None:
        chunks = [b"A\r\nB\r", b"\r\nC\r\n"]
        expected = b"A\r\nB\r\nC\r\n"
        self.seed(chunks, len(expected))
        for _ in range(2):
            result = self.restore()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Repaired 1 legacy CRLF", result.stdout)
            self.assertEqual((self.target / self.rel).read_bytes(), expected)
        for index, chunk in enumerate(chunks):
            path = self.state / f".parts/{self.rel}/part-{index:04d}"
            self.assertEqual(path.read_bytes(), chunk)

    def test_multiple_legacy_boundaries(self) -> None:
        expected = b"A\r\nB\r\nC\r\n"
        self.seed([b"A\r", b"\r\nB\r", b"\r\nC\r\n"], len(expected))
        result = self.restore()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Repaired 2 legacy CRLF", result.stdout)
        self.assertEqual((self.target / self.rel).read_bytes(), expected)

    def test_valid_data_including_double_cr_is_not_normalized(self) -> None:
        chunks = [b"A\r\r\nB\r", b"\r\nC\n"]
        expected = b"".join(chunks)
        self.seed(chunks, len(expected))
        result = self.restore()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Repaired", result.stdout)
        self.assertEqual((self.target / self.rel).read_bytes(), expected)

    def test_unexplained_size_mismatch_preserves_existing_output(self) -> None:
        self.seed([b"abc", b"def"], 5)
        self.assert_refused("expected 5, got 6")

    def test_wrong_boundary_count_is_not_repaired(self) -> None:
        self.seed([b"A\r", b"\r\nB"], 3)
        self.assert_refused("size mismatch")

    def test_internal_double_cr_is_not_repaired(self) -> None:
        self.seed([b"A\r\r\nB", b"C"], 5)
        self.assert_refused("size mismatch")

    def test_non_csv_is_not_repaired(self) -> None:
        self.rel = "data/raw/state.bin"
        self.seed([b"A\r", b"\r\nB"], 4)
        self.assert_refused("size mismatch")

    def test_lf_checkout_is_not_silently_normalized(self) -> None:
        self.seed([b"A\nB", b"\nC\n"], 9)
        self.assert_refused("size mismatch")

    def test_missing_part_preserves_existing_output(self) -> None:
        self.seed([b"abc", b"def"], 6)
        (self.state / f".parts/{self.rel}/part-0001").unlink()
        self.assert_refused("FileNotFoundError")

    def test_new_state_rejects_same_size_corruption(self) -> None:
        self.seed([b"abd"], 3, hashlib.sha256(b"abc").hexdigest())
        self.assert_refused("SHA-256 mismatch")

    def test_new_state_never_uses_legacy_repair(self) -> None:
        expected = b"A\r\nB"
        self.seed([b"A\r", b"\r\nB"], len(expected), hashlib.sha256(expected).hexdigest())
        self.assert_refused("size mismatch")

    def test_real_git_legacy_crlf_boundary(self) -> None:
        expected = b"A\r\nB\r\nC\r\nD\r\n"
        self.seed([expected[:5], expected[5:]], len(expected))
        self.commit_state()
        checkout = self.root / "windows-legacy-checkout"
        self.git("-c", "core.autocrlf=true", "clone", "--no-local", str(self.state), str(checkout))
        info = json.loads((checkout / "state-manifest.json").read_text())[self.rel]
        checked_out = b"".join((checkout / p).read_bytes() for p in info["parts"])
        self.assertEqual(len(checked_out), len(expected) + 1)
        result = self.restore(checkout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.target / self.rel).read_bytes(), expected)

    def test_new_pack_survives_git_on_windows_and_linux(self) -> None:
        expected = b"A\r\nB\r\nC\r\nD\r\n"
        model_rel = "data/dashboard_ews/us/model.json"
        model = self.target / model_rel
        model.parent.mkdir(parents=True)
        model.write_bytes(b"{}\r\n")  # Small, unsplit, checksum-sensitive text.
        source = self.target / self.rel
        source.parent.mkdir(parents=True)
        source.write_bytes(expected)
        self.state.mkdir()
        attributes = self.state / ".gitattributes"
        attributes.write_text("*.json text eol=lf\n")
        for _ in range(2):
            result = self.script(
                "pack_dashboard_state.py", "--state-dir", str(self.state),
                "--part-size", "5", "--paths", self.rel, model_rel,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(attributes.read_text(), "*.json text eol=lf\n.parts/** -text\ndata/dashboard_ews/** -text\ndata/** -text\noutputs/** -text\n")
        manifest = json.loads((self.state / "state-manifest.json").read_text())
        self.assertEqual(manifest[self.rel]["sha256"], hashlib.sha256(expected).hexdigest())
        self.commit_state()
        for autocrlf in ("true", "false"):
            checkout = self.root / f"checkout-{autocrlf}"
            self.git("-c", f"core.autocrlf={autocrlf}", "clone", "--no-local", str(self.state), str(checkout))
            for part in manifest[self.rel]["parts"]:
                self.assertIn("text: unset", self.git("check-attr", "text", "--", part, cwd=checkout))
                self.assertEqual((checkout / part).read_bytes(), (self.state / part).read_bytes())
            result = self.restore(checkout)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("Repaired", result.stdout)
            self.assertEqual(source.read_bytes(), expected)
            self.assertEqual(model.read_bytes(), b"{}\r\n")


if __name__ == "__main__":
    unittest.main()
