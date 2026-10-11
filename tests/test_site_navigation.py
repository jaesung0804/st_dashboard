import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from site_navigation import decorate, root_entry, sync

class NavigationTests(unittest.TestCase):
    def test_existing_shell_receives_papers_and_theme_without_touching_data(self):
        payload = '<script id="portfolio-data" type="application/json">{"private_field_not_read":"frozen"}</script>'
        original = '<html><head><meta charset="utf-8"><link rel="stylesheet" href="portfolio.css"></head><body>' + '<header id="st-site-header"><nav>old</nav><button id="theme">dark</button></header><main>unchanged</main>' + payload + '</body></html>'
        result = decorate(original, "portfolio/index.html")
        self.assertIn(payload, result)
        self.assertEqual(result.count('id="st-site-header"'), 1)
        self.assertEqual(result.count('data-st-theme="head"'), 1)
        self.assertIn('href="../papers/"', result)
        self.assertLess(result.index('data-st-theme="head"'), result.index('href="portfolio.css"'))
        self.assertEqual(decorate(result, "portfolio/index.html"), result)
        paper = decorate(original, "papers/index.html")
        self.assertIn('href="../papers/" aria-current="page"', paper)

    def test_partial_bundle_does_not_redirect_to_absent_portfolio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text("existing home")
            sync(root)
            self.assertEqual((root / "index.html").read_text(), "existing home")

    def test_shared_menu_preserves_theme_and_frozen_data(self):
        payload = '<script id="portfolio-data" type="application/json">{"frozen":"원본","value":1.2}</script>'
        source = '<html><head></head><body><header class="site-header"><nav>old</nav><button id="theme">dark</button></header><main id="main"></main>' + payload + '</body></html>'
        result = decorate(source, "portfolio/index.html")
        self.assertIn(payload, result)
        self.assertEqual(result.count('id="theme"'), 1)
        self.assertIn('href="../investment/"', result)
        self.assertIn('href="../research/"', result)
        self.assertEqual(decorate(result, "portfolio/index.html"), result)

    def test_reports_with_implicit_body_keep_their_content(self):
        source = '<html lang="ko"><meta charset="utf-8"><style>nav{display:flex}</style><main><h1>Report</h1><a href="./">Back</a></main>'
        result = decorate(source, "portfolio/return-risk.html")
        self.assertIn('<h1>Report</h1>', result)
        self.assertEqual(result.count('id="st-site-header"'), 1)
        self.assertLess(result.index('</style>'), result.index('site-nav.css'))

    def test_market_entry_redirects_and_forecasts_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "portfolio").mkdir()
            (root / "portfolio/index.html").write_text("<html><head></head><body><main>Portfolio</main></body></html>")
            market = root / "lgbm_warning_dashboard_macro_kr_latest"
            market.mkdir()
            (market / "index.html").write_text('<html data-investment-entry="v1">existing saved entry</html>')
            (market / "legacy.html").write_text('<html><head></head><body><main>Historical forecasts</main></body></html>')
            frozen = b'[{"ticker":"005930","upScore":35.33}]'
            (market / "rows.json").write_bytes(frozen)
            (market / "manifest.json").write_text(json.dumps({"uiAssets":{"legacy.html":"old","rows.json":hashlib.sha256(frozen).hexdigest()}}))
            sync(root)
            self.assertIn("existing saved entry", (market / "index.html").read_text())
            self.assertEqual((market / "rows.json").read_bytes(), frozen)
            manifest = json.loads((market / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["uiAssets"]["legacy.html"], hashlib.sha256((market / "legacy.html").read_bytes()).hexdigest())
            self.assertIn('data-portfolio-entry="v1"', (root / "index.html").read_text(encoding="utf-8"))
            self.assertTrue((root / "research/index.html").is_file())
            first = (market / "legacy.html").read_bytes()
            sync(root)
            self.assertEqual(first, (market / "legacy.html").read_bytes())

if __name__ == "__main__":
    unittest.main()

