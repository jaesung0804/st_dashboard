"""Source-bound local price caches for reproducible offline investment replays."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import pickle
import tempfile

import pandas as pd

from .investment_replay import read_prices


CACHE_VERSION = "investment-replay-price-cache-v1"
PRICE_COLUMNS = {"date", "ticker", "open", "high", "low", "close", "adjusted_close", "volume"}


class CacheIntegrityError(ValueError):
    """A cache cannot establish the requested input lineage."""


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def file_sha256(path):
    """Hash the actual file and reject changes visible during the hash read."""
    path = Path(path)
    before = path.stat()
    with path.open("rb") as handle:
        result = hashlib.file_digest(handle, "sha256").hexdigest()
    after = path.stat()
    signature = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    if signature(before) != signature(after):
        raise CacheIntegrityError(f"Input changed while hashing: {path}")
    return result


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".tmp-", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def cache_metadata_path(cache):
    return Path(str(cache) + ".metadata.json")


def _cohort(path, market):
    path = Path(path)
    content = path.read_bytes()
    cohort = json.loads(content)["markets"][market]
    if not cohort or any(not isinstance(ticker, str) or not ticker for ticker in cohort):
        raise CacheIntegrityError("The selected cohort must contain nonempty ticker strings")
    if len(cohort) != len(set(cohort)):
        raise CacheIntegrityError("Duplicate symbols in the requested cohort")
    symbols = sorted(ticker.zfill(6) if market == "kr" else ticker for ticker in cohort)
    if len(symbols) != len(set(symbols)):
        raise CacheIntegrityError("Duplicate normalized cohort symbols")
    info = {"path": str(path), "sha256": digest_bytes(content), "market": market,
            "tickers_sha256": digest_bytes(json.dumps(symbols, separators=(",", ":")).encode()),
            "ticker_count": len(symbols)}
    return symbols, info


def _reader_identity():
    return {"name": "read_prices", "implementation_sha256": digest_bytes(inspect.getsource(read_prices).encode()),
            "pandas_version": pd.__version__}


def _frame_summary(frame, symbols):
    if not isinstance(frame, pd.DataFrame) or not PRICE_COLUMNS.issubset(frame.columns) or frame.empty:
        raise CacheIntegrityError("Empty cache or missing price columns")
    if not pd.api.types.is_datetime64_any_dtype(frame.date) or frame.date.isna().any():
        raise CacheIntegrityError("Cache dates must be nonmissing parsed timestamps")
    if frame.duplicated(["date", "ticker"]).any():
        raise CacheIntegrityError("Duplicate price observations in cache")
    found = set(frame.ticker)
    if found != set(symbols):
        raise CacheIntegrityError(f"Cohort coverage mismatch; missing={sorted(set(symbols)-found)}, extra={sorted(found-set(symbols))}")
    return {"rows": len(frame), "ticker_count": len(found),
            "date_min": str(frame.date.min().date()), "date_max": str(frame.date.max().date()),
            "columns": list(frame.columns), "dtypes": frame.dtypes.astype(str).to_dict()}


def _metadata_checks(metadata, cohort_info, reader, source_sha):
    reasons = []
    if metadata.get("version") != CACHE_VERSION:
        reasons.append("cache_version_changed")
    if metadata.get("cache_kind") != "retained_cohort_selection":
        reasons.append("cache_kind_mismatch")
    previous = metadata.get("cohort", {})
    for key in ("sha256", "market", "tickers_sha256", "ticker_count"):
        if previous.get(key) != cohort_info[key]:
            reasons.append("cohort_changed")
            break
    if metadata.get("transform") != reader:
        reasons.append("reader_or_runtime_changed")
    recorded_source = metadata.get("source", {})
    if not recorded_source.get("sha256") or not recorded_source.get("verified_at"):
        reasons.append("source_lineage_missing")
    if source_sha is not None and recorded_source.get("sha256") != source_sha:
        reasons.append("source_changed")
    return reasons


def load_retained_prices(source, cohort_path, cache, market="us", *, allow_verified_offline_cache=False):
    """Return prices and measured provenance, rebuilding when inputs change.

    With a source present, its current SHA and the cohort/reader/cache fingerprints
    must match for a cache hit.  Missing sources fail by default; explicit offline
    use verifies the saved cache and labels the source as *not revalidated now*.
    Pickles are local artifacts, not a format for downloading untrusted inputs.
    """
    source, cohort_path, cache = Path(source), Path(cohort_path), Path(cache)
    sidecar = cache_metadata_path(cache)
    if {cache.resolve(), sidecar.resolve()} & {source.resolve(), cohort_path.resolve()}:
        raise CacheIntegrityError("Cache and metadata must not overwrite source or cohort inputs")
    symbols, cohort_info = _cohort(cohort_path, market)
    reader = _reader_identity()
    source_sha = file_sha256(source) if source.exists() else None
    reasons = []
    metadata, metadata_bytes, frame, payload = None, None, None, None
    if sidecar.exists() and cache.exists():
        try:
            metadata_bytes = sidecar.read_bytes()
            metadata = json.loads(metadata_bytes)
            reasons.extend(_metadata_checks(metadata, cohort_info, reader, source_sha))
            payload = cache.read_bytes()
            if digest_bytes(payload) != metadata.get("cache_sha256"):
                reasons.append("cache_bytes_changed")
            if not reasons:
                # Hash and parse the same bytes; a concurrent file replacement
                # cannot make the recorded digest describe a different payload.
                frame = pd.read_pickle(io.BytesIO(payload))
                if _frame_summary(frame, symbols) != metadata.get("frame"):
                    reasons.append("cache_frame_metadata_changed")
        except (ValueError, KeyError, TypeError, AttributeError, OSError, EOFError, pickle.UnpicklingError) as error:
            reasons.append("invalid_cache_metadata_or_payload:" + type(error).__name__)
    else:
        reasons.append("cache_or_metadata_missing")

    if source_sha is None:
        if not allow_verified_offline_cache:
            raise CacheIntegrityError("Retained source is unavailable; explicitly allow a verified offline cache or restore the source")
        if reasons or frame is None:
            raise CacheIntegrityError("Unavailable source and unverifiable cache: " + ", ".join(reasons))
        mode = "verified_cache_without_source"
    elif reasons or frame is None:
        frame = read_prices(source, symbols, market)
        summary = _frame_summary(frame, symbols)
        if file_sha256(source) != source_sha:
            raise CacheIntegrityError("Retained source changed while selecting the cohort")
        if digest_bytes(cohort_path.read_bytes()) != cohort_info["sha256"]:
            raise CacheIntegrityError("Cohort changed while selecting prices")
        buffer = io.BytesIO()
        frame.to_pickle(buffer)
        payload = buffer.getvalue()
        cache_sha = digest_bytes(payload)
        # Preserve identical cache bytes so active consumers are not disturbed.
        if not cache.exists() or file_sha256(cache) != cache_sha:
            _atomic_bytes(cache, payload)
        metadata = {"version": CACHE_VERSION, "cache_kind": "retained_cohort_selection", "created_at": _utc_now(),
                    "source": {"path": str(source), "sha256": source_sha, "bytes": source.stat().st_size, "verified_at": _utc_now()},
                    "cohort": cohort_info, "transform": reader, "cache_sha256": cache_sha, "frame": summary}
        metadata_bytes = (json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
        _atomic_bytes(sidecar, metadata_bytes)
        mode = "source_rebuilt_cache"
    else:
        mode = "source_verified_cache_hit"

    verified_at = _utc_now()
    provenance = {
        "mode": mode, "verified_at": verified_at, "source_available": source_sha is not None,
        "source_revalidated_this_run": source_sha is not None,
        "source_sha256": source_sha or metadata["source"]["sha256"],
        "source_path_requested": str(source),
        "source_last_verified_at": verified_at if source_sha is not None else metadata["source"]["verified_at"],
        "cohort_sha256": cohort_info["sha256"], "cohort_tickers_sha256": cohort_info["tickers_sha256"],
        "cache_path": str(cache), "cache_sha256": digest_bytes(payload), "cache_version": CACHE_VERSION,
        "cache_metadata_sha256": digest_bytes(metadata_bytes), "reader": reader,
        "cache_rebuild_reasons": sorted(set(reasons)), "frame": metadata["frame"],
    }
    return frame, provenance


def write_derived_price_cache(frame, cache, cohort_path, dependencies, *, market="us", transform_identity):
    """Save a local derived panel and its complete, caller-measured source map.

    The V2 runner rebuilds this long panel from measured inputs on every invocation;
    this sidecar records its provenance for future consumers, without claiming that
    older scripts which read the pickle directly have verified the sidecar.
    """
    symbols, cohort_info = _cohort(cohort_path, market)
    if {Path(cache).resolve(), cache_metadata_path(cache).resolve()} & {Path(cohort_path).resolve()}:
        raise CacheIntegrityError("Derived cache must not overwrite its cohort input")
    summary = _frame_summary(frame, symbols)
    if not dependencies or not transform_identity:
        raise CacheIntegrityError("Derived caches require explicit inputs and transform identity")
    buffer = io.BytesIO()
    frame.to_pickle(buffer)
    payload = buffer.getvalue()
    cache = Path(cache)
    digest = digest_bytes(payload)
    if not cache.exists() or file_sha256(cache) != digest:
        _atomic_bytes(cache, payload)
    metadata = {"version": CACHE_VERSION, "cache_kind": "derived_long_cohort", "created_at": _utc_now(),
                "cache_sha256": digest, "cohort": cohort_info, "frame": summary,
                "dependencies": dependencies, "transform": transform_identity}
    sidecar = cache_metadata_path(cache)
    _atomic_bytes(sidecar, (json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode())
    return {"cache_path": str(cache), "cache_sha256": digest, "cache_version": CACHE_VERSION,
            "cache_metadata_sha256": file_sha256(sidecar), "recreated_from_declared_inputs": True,
            "cohort_sha256": cohort_info["sha256"], "frame": summary}
