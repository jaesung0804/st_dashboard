"""Read-only inventory of immutable research artifacts; never runs model code."""
import hashlib
import io
import json
import os
import tarfile
import zipfile
from research_backend_client import Client

ARCHIVES = {
    "baseline_50000": "c585de95adbef39d8f30db93727b5f83dbcd4c96afcc4551d4443c9ad0497041",
    "baseline_risk_review": "d15ec05e5e74e4f69c1bdcc0e0de612c042b6510d7afbd1fc7ca53ea82d71bc4",
    "inputs": "aae4440a18f2e5064cda6b70bc330933ec6dec9adb12b8c852a1eb8f8c530012",
}

class ReadOnlyClient(Client):
    def request(self, method, path, body=None, file=None):
        if method != "GET" or not path.startswith("/files/"):
            raise ValueError("Only immutable artifact reads are allowed")
        return super().request(method, path, body, file)

def main():
    if not all(os.environ.get(k) for k in ("RESEARCH_BACKEND_URL", "RESEARCH_BACKEND_TOKEN")):
        print(json.dumps({"status": "backend_not_configured"})); return 1
    client = ReadOnlyClient(project="investment")
    for label, sha in ARCHIVES.items():
        info = client.json("GET", f"/files/{sha}/info")
        size = info["byte_size"]
        if size > 128 * 1024 * 1024:
            print(json.dumps({"archive":label,"status":"size_cap","bytes":size})); continue
        with client.request("GET", f"/files/{sha}") as response:
            raw = response.read(size + 1)
        assert len(raw) == size and hashlib.sha256(raw).hexdigest() == sha
        members = []
        if zipfile.is_zipfile(io.BytesIO(raw)):
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                members = [{"path":item.filename,"bytes":item.file_size} for item in archive.infolist()]
        elif raw[:1] in (b"{", b"["):
            value = json.loads(raw)
            members = [{"json_keys":list(value) if isinstance(value,dict) else ["list"],"length":len(value)}]
        else:
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
                members = [{"path":item.name,"bytes":item.size} for item in archive.getmembers() if item.isfile()]
        print("ARCHIVE_INVENTORY " + json.dumps({"archive":label,"sha256":sha,"bytes":size,"members":members},ensure_ascii=False))
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"error_type":type(error).__name__,"http_status":getattr(error,"status",None)}))
        raise SystemExit(1)
