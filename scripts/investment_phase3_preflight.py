"""Read-only recovery of the investment user's third order (internal r04).

Only GET is allowed. Do not print credentials, source payloads or agent history.
No migration, model training, collection, order, file upload or retention job.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.parse
from datetime import datetime, timezone

from research_backend_client import BackendError, Client

INPUTS = {
    "cohort_cache": "b82e69df5e9fd379e54ef5b9a45bcb9a55b52417c395ac3c34ccd2bb609480db",
    "filing_events": "48320d02173e72e42c49b347a6608889d766c4491b9d834a81b495113977208c",
    "spy_prices": "0a0b1d69cc1a6824e08176b1884df4d31f5a3ebd090a312a4eb452024f9ac7c3",
    "cohort": "2231313d486647c1d44c29b2915552b230cbafb2e08158dd444e4a53ce4a1696",
    "raw_us_prices": "d20459d3205b7694d2e77068bd7e1d7e329c970e3c0d2f46c17f0b334110d94a",
}


class ReadOnlyClient(Client):
    def request(self, method, path, body=None, file=None):
        if method != "GET" or body is not None or file is not None:
            raise ValueError("Phase 3 preflight permits GET only")
        return super().request(method, path)


def maybe(client, path):
    try:
        return client.json("GET", path)
    except BackendError as error:
        if error.status == 404:
            return None
        raise


def inspect_backend(client):
    if client.project != "investment":
        raise ValueError("Wrong project")
    ready = client.json("GET", "/ready")
    if ready.get("database") != "oracle" or ready.get("blob_store") != "oci":
        raise ValueError("Unexpected backend")
    report = {
        "schema": "investment-phase3-preflight-v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "project": "investment", "user_round": 3, "internal_round": "r04",
        "database": "oracle", "files": "oci", "read_only": True,
        "db_capacity_bytes": None, "oci_capacity_bytes": None,
        "capacity_status": "not exposed by this project API; uploads remain disabled",
        "paid_calls": 0, "db_writes": 0, "deletions": 0,
    }
    # Start with small memory pages, then read only the selected round document.
    report["record_pages"] = {}
    for kind in ("rounds", "checkpoints"):
        page = client.json("GET", f"/records/{kind}?limit=20")
        items = page.get("items", [])
        report["record_pages"][kind] = {
            "keys": [str(x.get("key", x.get("record_key", x.get("id", "")))) for x in items],
            "more": page.get("next_cursor") is not None,
        }
    rounds = client.read_json("data/reference/investment_rounds.json")
    report["rounds"] = [{
        "id": x.get("id"), "status": x.get("status"),
        "experiment_count": len(x.get("experiments", [])),
        "completed_experiments": sum(e.get("status") == "completed" for e in x.get("experiments", [])),
        "strategy_employee_count": len(x.get("strategy_employees", [])),
    } for x in rounds.get("rounds", [])][-8:]
    head = client.json("GET", "/snapshot-heads/pipeline-state")
    sid = head.get("snapshot_id")
    report["pipeline_snapshot_present"] = bool(sid)
    report["exact_input_blobs"] = {}
    for name, sha in INPUTS.items():
        info = maybe(client, f"/files/{sha}/info")
        report["exact_input_blobs"][name] = {
            "sha256": sha, "present": info is not None,
            "byte_size": info.get("byte_size") if info else None,
        }
    if sid:
        # Bound inspection independently of the total retained history size.
        entries, cursor = [], ""
        for _ in range(10):
            page = client.json("GET", f"/snapshots/{sid}/files?" + urllib.parse.urlencode({"after": cursor}))
            entries.extend(page.get("items", []))
            cursor = page.get("next_cursor")
            if cursor is None:
                break
        report["snapshot_inspection_complete"] = cursor is None
        report["snapshot_file_count"] = len(entries)
        report["snapshot_inspected_bytes"] = sum(int(e["byte_size"]) for e in entries)
        manifest = next((e for e in entries if e["relative_path"] in ("manifest.json", "state-manifest.json")), None)
        report["pipeline_manifest_found"] = manifest is not None
        if manifest and int(manifest["byte_size"]) <= 500_000:
            with client.request("GET", "/files/" + manifest["sha256"]) as response:
                raw = response.read(500_001)
            if len(raw) != manifest["byte_size"] or hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
                raise ValueError("Invalid pipeline manifest")
            body = json.loads(raw)
            report["manifest_top_keys"] = sorted(body)
            values = body.get("files", {})
            report["manifest_files_type"] = type(values).__name__
            if isinstance(values, dict):
                report["replay_input_candidates"] = {
                    path: {k: value.get(k) for k in ("sha256", "size", "encoding", "parts")}
                    for path, value in values.items()
                    if any(word in path.lower() for word in ("us_ohlcv", "replay", "filing_events", "spy", "accounting_cohort"))
                }
            elif isinstance(values, list):
                report["replay_input_candidates"] = [value for value in values
                    if any(word in json.dumps(value).lower() for word in ("us_ohlcv", "replay", "filing_events", "spy", "accounting_cohort"))][:12]
        report["related_snapshot_entries"] = [{k: e[k] for k in ("relative_path", "sha256", "byte_size")}
            for e in entries if any(w in e["relative_path"].lower() for w in ("manifest", "replay", "filing", "spy", "cohort"))][:30]
    report["can_run_exact_replay_from_direct_blobs"] = all(x["present"] for x in report["exact_input_blobs"].values())
    return report


def main():
    missing = [k for k in ("RESEARCH_BACKEND_URL", "RESEARCH_BACKEND_TOKEN") if not os.environ.get(k)]
    if missing or os.environ.get("RESEARCH_STORAGE") != "backend":
        print(json.dumps({"project": "investment", "status": "blocked_configuration", "missing": missing, "writes": 0}))
        return 2
    try:
        report = inspect_backend(ReadOnlyClient(project="investment"))
        print("PHASE3_PREFLIGHT=" + json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        # Do not log exception text: transport messages can contain private URLs.
        safe = {"project": "investment", "status": "preflight_failed", "error_type": type(error).__name__, "writes": 0}
        if isinstance(error, BackendError):
            safe["http_status"] = error.status
        print(json.dumps(safe))
        return 1


if __name__ == "__main__":
    sys.exit(main())
