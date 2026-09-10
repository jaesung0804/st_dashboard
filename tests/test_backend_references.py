from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from backend_references import References
from research_backend_client import BackendError

ORG = "data/reference/investment_organization.json"
QUEUE = "data/reference/investment_operations_queue.json"


class ReferenceTests(unittest.TestCase):
    def test_backend_reads_and_atomic_writes_keep_tracked_json_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"RESEARCH_STORAGE": "backend"}):
            root = Path(temporary)
            original = '{"stale_git_fixture": true}'
            for relative in (ORG, QUEUE):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(original, encoding="utf-8")
            remote = {ORG: {"companies": {}}, QUEUE: {"tasks": [{"id": "test", "status": "open"}]}}
            client = Mock()
            client.read_json.side_effect = lambda relative: deepcopy(remote[relative])
            client.write_references.return_value = {"changed": True}
            references = References(root, client)
            values = {path: references.read(path) for path in remote}
            self.assertEqual(values, remote)
            client.write_references.assert_not_called()
            values[QUEUE]["tasks"][0]["status"] = "reviewed"
            references.write_many(values)
            client.write_references.assert_called_once_with(values)
            self.assertTrue(all((root / path).read_text() == original for path in remote))
            client.write_references.side_effect = BackendError(409, "Concurrent update")
            with self.assertRaises(BackendError):
                references.write_many(values)
            self.assertTrue(all((root / path).read_text() == original for path in remote))

    def test_backend_missing_reference_does_not_fall_back_to_git_and_legacy_still_works(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(os.environ, {"RESEARCH_STORAGE": "git"}):
                legacy = References(root)
                legacy.write(QUEUE, {"tasks": [{"id": "local"}]})
                self.assertEqual(legacy.read(QUEUE)["tasks"][0]["id"], "local")
            client = Mock()
            client.read_json.side_effect = BackendError(404, "No document")
            with patch.dict(os.environ, {"RESEARCH_STORAGE": "backend"}):
                references = References(root, client)
                with self.assertRaises(BackendError):
                    references.read(QUEUE)
            self.assertEqual(json.loads((root / QUEUE).read_text())["tasks"][0]["id"], "local")

    def test_new_research_output_is_ignored_and_published_without_local_receipts(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"RESEARCH_STORAGE": "backend"}):
            root = Path(temporary)
            logical = "docs/replays/synthetic-run"
            client = Mock()
            references = References(root, client)
            output = references.output(logical)
            self.assertTrue(output.is_relative_to(root / ".research-backend"))
            output.mkdir(parents=True)
            (output / "protocol.json").write_text('{"synthetic": true}')
            receipts = output / ".research-backend"
            receipts.mkdir()
            (receipts / "local.json").write_text("{}")
            references.publish_output(output)
            name, actual, files = client.push.call_args.args
            self.assertTrue(name.startswith("research-"))
            self.assertEqual(actual, output)
            self.assertEqual(files, ["protocol.json"])
            self.assertEqual(references.artifact_reference(output / "protocol.json"), logical + "/protocol.json")
            self.assertFalse((root / logical).exists())
            client.write_references.assert_not_called()
            client.push.side_effect = BackendError(409, "Snapshot head changed")
            with self.assertRaises(BackendError):
                references.publish_output(output)
            client.pull.assert_not_called()

    def test_restoring_old_immutable_artifacts_preserves_git_and_reads_do_not_publish(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"RESEARCH_STORAGE": "backend"}):
            root = Path(temporary)
            logical = "docs/research_library/synthetic-import"
            historical = root / logical
            historical.mkdir(parents=True)
            (historical / "source.json.gz").write_bytes(b"synthetic retained bytes")
            client = Mock()
            def absent_snapshot(name, output):
                Path(output).mkdir(parents=True, exist_ok=True)
                raise BackendError(404, "No snapshot")
            client.pull.side_effect = absent_snapshot
            references = References(root, client)
            self.assertEqual(references.restore_output(logical), historical)
            output = references.output(logical, resume=True)
            self.assertEqual((output / "source.json.gz").read_bytes(), b"synthetic retained bytes")
            self.assertEqual((historical / "source.json.gz").read_bytes(), b"synthetic retained bytes")
            client.push.assert_not_called()
            client.write_references.assert_not_called()


if __name__ == "__main__":
    unittest.main()
