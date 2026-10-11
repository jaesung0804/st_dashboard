"""Apply one navigation contract to existing public pages without rerunning research."""
from __future__ import annotations
import argparse
import hashlib
import html
import json
import re
from pathlib import Path
VERSION = "20261011"
ASSETS = Path(__file__).resolve().parent / "dashboard_web"

def header(prefix: str, active: str, theme: str = "") -> str:
    items = (("portfolio", "portfolio/", "포트폴리오"), ("investment", "investment/", "장기 후보"), ("research", "research/", "연구 기록"))
    links = "".join(f'<a href="{prefix}{path}"' + (' aria-current="page"' if key == active else '') + f'>{label}</a>' for key, path, label in items)
    return f'<header id="st-site-header"><div class="st-header-inner"><a class="st-brand" href="{prefix}portfolio/">ST <strong>Research</strong></a><nav class="st-primary" aria-label="주요 메뉴">{links}</nav>{theme}</div></header>'

def root_entry() -> str:
    return """<!doctype html><html lang="ko" data-portfolio-entry="v1"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ST Research · 포트폴리오</title><link rel="stylesheet" href="site-nav.css?v=20261011">
<script>(()=>{const q=new URLSearchParams(location.search);const stock=q.get('q')||q.get('ticker');
const target=new URL(stock?'investment/':'portfolio/',location.href);
if(stock)target.searchParams.set('q',stock.slice(0,100));else target.hash=location.hash;
location.replace(target.href);})();</script>
<noscript><meta http-equiv="refresh" content="0;url=portfolio/"></noscript></head>
<body><p><a href="portfolio/">포트폴리오 열기</a> · <a href="investment/">장기 투자 후보 찾기</a> · <a href="research/">연구 기록</a></p></body></html>
"""

def decorate(text: str, relative: str) -> str:
    prefix = "../" * (len(Path(relative).parts) - 1)
    active = "portfolio" if relative == "portfolio/index.html" else ("investment" if relative.startswith("investment/") or "lgbm_" in relative else "research")
    # Idempotence keeps scheduled publishers from adding duplicate headers.
    if 'id="st-site-header"' in text:
        return text
    theme_match = re.search(r'<button\b[^>]*id="(?:theme|themeToggle)"[^>]*>.*?</button>', text, re.S)
    theme = theme_match.group(0) if theme_match else ""
    shared = header(prefix, active, theme)
    old_header = re.search(r'<header class="(?:app-header|site-header)">.*?</header>', text, re.S)
    local = ""
    if relative == "portfolio/index.html":
        local = '<details class="st-section-nav"><summary>포트폴리오 목차</summary><nav aria-label="포트폴리오 목차"><a href="#account">가상 계좌</a><a href="#readiness">운용 준비</a><a href="#evidence">검증 결과</a><a href="model.html">모델 구조</a></nav></details>'
    elif relative == "investment/index.html":
        local = '<details class="st-section-nav"><summary>후보 선별 목차</summary><nav aria-label="후보 선별 목차"><a href="#comparison">종목 탐색</a><a href="#validation">검증 결과</a><a href="#method">산정 기준</a></nav></details>'
    if old_header:
        text = text[:old_header.start()] + shared + local + text[old_header.end():]
    else:
        text = re.sub(r'(<body[^>]*>)(<nav\b[^>]*>.*?</nav>)',
                      lambda m: m.group(1) + '<details class="st-section-nav"><summary>이 페이지 목차</summary>' + m.group(2) + '</details>',
                      text, count=1, flags=re.S)
        text, count = re.subn(r'(<body\b[^>]*>)', lambda m: m.group(1) + shared, text, count=1, flags=re.I)
        if not count:
            # Some published research documents omit explicit head/body tags.
            pos = text.find("<main")
            if pos < 0:
                raise ValueError(f"No page body: {relative}")
            text = text[:pos] + shared + text[pos:]
    css = f'<link rel="stylesheet" href="{prefix}site-nav.css?v={VERSION}">'
    if "</head>" in text:
        text = text.replace("</head>", css + "</head>", 1)
    else:
        # Insert after existing styles so unscoped paper CSS cannot override the shell.
        pos = text.rfind("</style>")
        text = text[:pos+8] + css + text[pos+8:] if pos >= 0 else css + text
    if relative == "investment/index.html":
        text = text.replace("ST Research · 한·미 투자매력도 연구", "장기 투자 후보 · ST Research")
        text = text.replace("한국과 미국,<br><span>한 화면에서.</span>", "장기 투자 후보를,<br><span>한 곳에서 선별.</span>")
        text = text.replace("통합 매력도부터 실적과 현금흐름까지.<br>숫자를 비교하고, 종목을 눌러 근거를 확인하세요.", "기존 모형으로 먼저 살펴볼 종목을 추립니다.<br>장기 투자 판단은 재무·사업·가격 검토로 이어갑니다.")
        text = text.replace("원화 기준 통합 점수 · 약 3개월 전망", "장기 투자 후보 선별용 · 기존 약 3개월 전망")
    return text

def research_page() -> str:
    return """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>연구 기록 · ST Research</title><style>body{margin:0;background:#f5f7f9}</style></head><body><main class="st-research-main"><p>ST RESEARCH · RESEARCH ARCHIVE</p><h1>연구 결과와 이전 기록</h1><p>완료된 실험과 설명은 이곳에서 찾습니다. 메인 포트폴리오의 현재 상태와 각 실험의 기간·가정은 구분해서 확인하세요.</p><div class="st-cards">
<article><h2>포트폴리오 모델</h2><p>수익 예측, 비중 결정과 남아 있는 구현 과제를 확인합니다.</p><a href="../portfolio/model.html">모델 구조·검증 현황 →</a></article>
<article><h2>정수 주수·원화 총자산</h2><p>2025.10–2026.09 · 여섯 계좌의 적립식 성과, 비용과 체결 후 위험.</p><a href="../portfolio/whole-wealth-paper.html">최근 완료 보고서 →</a></article>
<article><h2>후보 선택 비교</h2><p>같은 점수의 종목을 포함하는 방식에 따른 여덟 조건 비교.</p><a href="../portfolio/candidate-comparison.html">후보 선택 실험 →</a></article>
<article><h2>기존 포트폴리오 실험</h2><p>이전 설계와 수익·변동성 비교를 보존합니다.</p><a href="../portfolio/paper.html">기존 보고서 →</a><br><a href="../portfolio/return-risk.html">수익·위험 비교 →</a></article>
<article><h2>과거 예측 기록</h2><p>한국·미국 기존 모형의 날짜별 신호와 검증 기록.</p><a href="../history.html">예측 기록 →</a></article>
<article><h2>투자 시뮬레이션</h2><p>기존 회사·팀별 전략 비교와 운영 기록.</p><a href="../simulation/">시뮬레이션 →</a></article>
</div></main></body></html>"""

def sync(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "site-nav.css").write_bytes((ASSETS / "site-nav.css").read_bytes())
    # Upgrade all existing HTML, including standalone experimental reports.
    for path in root.rglob("*.html"):
        rel = path.relative_to(root).as_posix()
        if rel == "index.html":
            continue
        original = path.read_text(encoding="utf-8")
        if 'data-investment-entry="v1"' in original:
            continue
        revised = decorate(original, rel)
        if revised != original:
            path.write_bytes(revised.encode("utf-8"))
    archive = root / "research" / "index.html"
    archive.parent.mkdir(exist_ok=True)
    archive.write_bytes(decorate(research_page(), "research/index.html").encode("utf-8"))
    (root / "index.html").write_bytes(root_entry().encode("utf-8"))
    for path in root.glob("lgbm_*/manifest.json"):
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
        for name in manifest.get("uiAssets", {}):
            asset = path.parent / name
            if asset.is_file():
                manifest["uiAssets"][name] = hashlib.sha256(asset.read_bytes()).hexdigest()
        path.write_bytes(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    sync(parser.parse_args().directory)

