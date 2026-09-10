"""Check source/cohort changes and tampering, not just cache implementation."""
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from ai_stock_assistant.investment_replay_inputs import (
    CacheIntegrityError, cache_metadata_path, file_sha256, load_retained_prices,
    write_derived_price_cache,
)


class ReplayInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.source, self.cohort, self.cache = root / "prices.csv", root / "cohort.json", root / "cache.pkl"
        self.prices = pd.DataFrame([
            {"date": date, "ticker": ticker, "open": price, "high": price + 1, "low": price - 1,
             "close": price, "adjusted_close": price, "volume": 1000.0}
            for date in ("2021-01-04", "2021-01-05") for ticker, price in (("AAA", 100.0), ("BBB", 200.0))
        ])
        self.prices.to_csv(self.source, index=False)
        self.set_cohort(["AAA"])

    def set_cohort(self, tickers):
        self.cohort.write_text(json.dumps({"markets": {"us": tickers}}))

    def load(self, **options):
        return load_retained_prices(self.source, self.cohort, self.cache, **options)

    def test_measured_hashes_and_unchanged_cache_hit(self):
        first, provenance = self.load()
        cache_time = self.cache.stat().st_mtime_ns
        self.assertEqual(provenance["mode"], "source_rebuilt_cache")
        self.assertEqual(provenance["source_sha256"], file_sha256(self.source))
        self.assertEqual(provenance["cohort_sha256"], file_sha256(self.cohort))
        self.assertEqual(provenance["cache_sha256"], file_sha256(self.cache))
        second, hit = self.load()
        pd.testing.assert_frame_equal(first, second)
        self.assertEqual(hit["mode"], "source_verified_cache_hit")
        self.assertEqual(self.cache.stat().st_mtime_ns, cache_time)

    def test_changed_source_and_changed_cohort_reselect(self):
        _, original = self.load()
        self.prices.loc[self.prices.ticker.eq("AAA"), "close"] = 321.0
        self.prices.to_csv(self.source, index=False)
        changed, provenance = self.load()
        self.assertEqual(set(changed.close), {321.0})
        self.assertIn("source_changed", provenance["cache_rebuild_reasons"])
        self.assertNotEqual(provenance["source_sha256"], original["source_sha256"])
        self.set_cohort(["BBB"])
        changed, provenance = self.load()
        self.assertEqual(set(changed.ticker), {"BBB"})
        self.assertIn("cohort_changed", provenance["cache_rebuild_reasons"])

    def test_tampered_cache_rebuilds_from_source_and_fails_without_it(self):
        original, _ = self.load()
        tampered = original.copy()
        tampered["close"] = 999999.0
        tampered.to_pickle(self.cache)
        rebuilt, provenance = self.load()
        pd.testing.assert_frame_equal(rebuilt, original)
        self.assertIn("cache_bytes_changed", provenance["cache_rebuild_reasons"])
        tampered.to_pickle(self.cache)
        self.source.unlink()
        with self.assertRaises(CacheIntegrityError):
            self.load(allow_verified_offline_cache=True)

    def test_absent_source_needs_explicit_verified_offline_use(self):
        first, original = self.load()
        self.source.unlink()
        with self.assertRaises(CacheIntegrityError):
            self.load()
        cached, provenance = self.load(allow_verified_offline_cache=True)
        pd.testing.assert_frame_equal(first, cached)
        self.assertEqual(provenance["mode"], "verified_cache_without_source")
        self.assertFalse(provenance["source_revalidated_this_run"])
        self.assertEqual(provenance["source_sha256"], original["source_sha256"])
        self.set_cohort(["BBB"])
        with self.assertRaises(CacheIntegrityError):
            self.load(allow_verified_offline_cache=True)

    def test_missing_metadata_or_changed_version_cannot_silently_hit(self):
        self.load()
        sidecar = cache_metadata_path(self.cache)
        sidecar.unlink()
        _, provenance = self.load()
        self.assertIn("cache_or_metadata_missing", provenance["cache_rebuild_reasons"])
        metadata = json.loads(sidecar.read_text())
        metadata["version"] = "unrecognized-version"
        sidecar.write_text(json.dumps(metadata))
        _, provenance = self.load()
        self.assertIn("cache_version_changed", provenance["cache_rebuild_reasons"])
        self.set_cohort(["DOES_NOT_EXIST"])
        with self.assertRaises(CacheIntegrityError):
            self.load()

    def test_derived_cache_records_dependency_evidence(self):
        frame, retained = self.load()
        path = self.cache.with_name("derived.pkl")
        provenance = write_derived_price_cache(frame, path, self.cohort,
            {"retained": retained}, transform_identity={"rule": "synthetic-test"})
        self.assertEqual(provenance["cache_sha256"], file_sha256(path))
        metadata = json.loads(cache_metadata_path(path).read_text())
        self.assertEqual(metadata["dependencies"]["retained"]["source_sha256"], retained["source_sha256"])

    def test_cache_path_cannot_overwrite_original_inputs(self):
        original = self.source.read_bytes()
        with self.assertRaises(CacheIntegrityError):
            load_retained_prices(self.source, self.cohort, self.source)
        self.assertEqual(self.source.read_bytes(), original)
        with self.assertRaises(CacheIntegrityError):
            load_retained_prices(self.source, self.cohort, self.cohort)


if __name__ == "__main__":
    unittest.main()
