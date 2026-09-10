"""Reference documents use one CAS transaction in backend mode; reads never run jobs."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil

from research_backend_client import BackendError, Client, REFERENCE_KINDS

_MISSING = object()


class References:
    def __init__(self, root=None, client=None):
        self.root = Path(root or Path(__file__).resolve().parents[1]).resolve()
        self.backend = os.getenv("RESEARCH_STORAGE", "git") == "backend"
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = Client(project="investment")
        return self._client

    def relative(self, path):
        path = Path(path)
        target = path if path.is_absolute() else self.root / path
        try:
            relative = target.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return None
        return relative if relative == "data/reference/" + target.name and target.name in REFERENCE_KINDS else None

    def read(self, path, default=_MISSING):
        relative = self.relative(path)
        if self.backend and relative:
            try:
                return self.client.read_json(relative)
            except BackendError as error:
                if error.status != 404 or default is _MISSING:
                    raise
                self.client.versions[relative] = 0
                self.client.reference_bases[relative] = None
                return deepcopy(default)
        target = Path(path) if Path(path).is_absolute() else self.root / path
        if not target.exists() and default is not _MISSING:
            return deepcopy(default)
        return json.loads(target.read_text(encoding="utf-8-sig"))

    def write_many(self, updates):
        if self.backend:
            normalized = {self.relative(path): value for path, value in updates.items()}
            if None in normalized or len(normalized) != len(updates):
                raise ValueError("Only known reference documents can enter the backend transaction")
            # Successful writes stay in Oracle/Object Storage. Tracked JSON files
            # are not rewritten or silently used as a fallback after a conflict.
            return self.client.write_references(normalized)
        for path, value in updates.items():
            target = Path(path) if Path(path).is_absolute() else self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(target)
        return {"changed": True}

    def write(self, path, value):
        return self.write_many({path: value})

    def _artifact_locations(self, path):
        target = Path(path)
        target = target if target.is_absolute() else self.root / target
        logical = target.resolve().relative_to(self.root).as_posix()
        prefix = ".research-backend/artifacts/"
        if logical.startswith(prefix):
            logical = logical[len(prefix):]
        if not logical.startswith(("docs/replays/", "docs/research_library/")):
            raise ValueError("Research artifact path must name a replay or research library folder")
        cached = self.root / prefix / logical
        if not cached.resolve().is_relative_to(self.root / ".research-backend"):
            raise ValueError("Research artifact path leaves its cache")
        return logical, cached

    def artifact_reference(self, path):
        return self._artifact_locations(path)[0] if self.backend else str(path)

    def _artifact_name(self, logical):
        return "research-" + hashlib.sha256(logical.encode()).hexdigest()

    def output(self, path, resume=False):
        """Prepare an explicit batch output; backend output never enters tracked docs."""
        if not self.backend:
            return Path(path)
        logical, cached = self._artifact_locations(path)
        if resume:
            try:
                self.client.pull(self._artifact_name(logical), cached)
            except BackendError as error:
                if error.status != 404:
                    raise
                legacy = self.root / logical
                if legacy.is_dir() and (not cached.exists() or not any(cached.iterdir())):
                    shutil.copytree(legacy, cached, dirs_exist_ok=True)
        elif cached.exists() or (self.root / logical).exists():
            raise ValueError("Choose a new output folder; existing research is retained")
        return cached

    def publish_output(self, path):
        if not self.backend:
            return None
        logical, cached = self._artifact_locations(path)
        if Path(path).resolve() != cached.resolve():
            raise ValueError("Backend research must be written in the ignored artifact cache")
        # Receipt/head CAS preserves prior snapshots. A conflict is not retried
        # by pulling over this run's new files. No repacking or collection here.
        paths = sorted(item.name for item in cached.iterdir() if item.name != ".research-backend")
        if not paths:
            raise ValueError("Cannot publish an empty research output")
        return self.client.push(self._artifact_name(logical), cached, paths)

    def restore_output(self, path):
        """Restore a named saved result for an explicit batch/read, never run a job."""
        if not self.backend:
            return Path(path)
        logical, cached = self._artifact_locations(path)
        try:
            self.client.pull(self._artifact_name(logical), cached)
            return cached
        except BackendError as error:
            # Historical immutable bundles remain available in the source repo.
            # This fallback is solely for artifacts, never mutable reference JSON.
            if error.status != 404 or not (self.root / logical).is_dir():
                raise
            return self.root / logical

    def artifact_file(self, path):
        if self.backend:
            logical, cached = self._artifact_locations(path)
            if cached.is_file():
                return cached
            return self.root / logical
        return self.root / path
