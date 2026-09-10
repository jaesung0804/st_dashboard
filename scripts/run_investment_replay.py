"""Run the first investment-company experiment using already retained data."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ai_stock_assistant.investment_replay import prepare, read_prices, replay, sha256, write_json


def write_gzip(path, content):
    with path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as out:
            out.write(content.encode("utf-8"))


def ledger(records):
    previous = "0" * 64
    lines = []
    for index, record in enumerate(records):
        row = {"sequence": index, "previous_hash": previous, **record}
        body = json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)
        previous = hashlib.sha256(body.encode()).hexdigest()
        lines.append(json.dumps({**row, "hash": previous}, ensure_ascii=False, allow_nan=False))
    return "\n".join(lines) + "\n", previous


def report(bundle, elapsed):
    lines = ["# 투자회사 과거 재생 · 첫 실행", "", "이 결과는 사전 정의된 규칙의 연구 시뮬레이션이다. 실제 AI 에이전트의 과거 판단이나 검증된 투자 추천을 뜻하지 않는다.", "",
             f"기간: {bundle['policy']['start']}–{bundle['policy']['end']} · 처리 시간: {elapsed:.2f}초 · 외부 AI API 호출 0회", "",
             "회사당 3개 팀에 각 30%, 본사 현금 10%. 팀별 최대 10종목·종목당 팀 자산 10%. 회사/팀 자금 재배분은 첫 비교에서 고정한다.", "",
             "| 시장 | 회사/비교군 | 누적 순수익률 | 최대낙폭 | 비용 2배 순수익률 | 상태 |", "|---|---|---:|---:|---:|---|"]
    names = {"pulse": "펄스", "compound": "컴파운드", "adaptive": "어댑티브", "baseline_equal_weight": "동일 표본 동일가중", "baseline_momentum": "단순 20일 모멘텀"}
    for market, result in bundle["markets"].items():
        for key, name in names.items():
            v, stress = result["summary"][key], result["stress_summary"][key]
            warning = v.get("stale_team_days", v.get("stale_exposure_days", 0)) + v.get("price_break_team_days", v.get("price_break_exposure_days", 0))
            state = "가격 누락/변동 점검 필요" if warning else "표본·배당 등 한계 있음"
            lines.append(f"| {market.upper()} | {name} | {v['net_return']:.2%} | {v['max_drawdown']:.2%} | {stress['net_return']:.2%} | {state} |")
    lines += ["", "## 해석 제한", "",
              "- 기존 연구 표본 128개씩을 재사용했다. 선정은 2022년 말 유동성 기반이나 보존된 현재 상장종목 표본이므로 생존편향이 남아 있다.",
              "- 현재 산업분류를 과거 신호에 소급 적용하지 않았다. 당시 분류가 없어 이번 실행의 섹터 한도는 미검증이다.",
              "- 동일가중 비교군은 같은 표본의 통제군이다. KOSPI/S&P 500 같은 공식 시장 지수가 아니므로 시장을 이겼다는 주장을 할 수 없다.",
              "- 한국은 보존된 수정주가 기준이며 현금배당을 별도로 합산하지 못했다. 미국은 공급자의 adjusted close를 사용한다. 국가 간 수익률을 합산하지 않는다.",
              "- 편도 비용은 한국 25bp·미국 10bp의 연구 가정이다. 실제 증권사 수수료/거래세/슬리피지를 정밀 재현하지 않는다. 현금 이자는 0이다.",
              "- 조정 가격 단위의 소수 수량을 사용한다. 정수 주식·호가단위·가격제한 잠김·장중 체결 우선순위는 아직 재현하지 않는다.",
              "- 원장에 없는 보유종목을 사후 삭제하지 않는다. 5거래일 넘게 가격이 없으면 마지막 관측가 평가를 유지하고 성과에 미확정 표시를 한다.",
              "- 미래 가격이나 future_return 같은 사후 라벨은 의사결정 입력으로 받지 않는다. 공시 정보는 접수 다음 날 이후에만 연결한다.",
              "- 2025년 이후 구간도 이번에 살펴보는 과거 확인 구간이다. 새롭고 독립적인 전진 검증이라고 부르지 않는다.",
              "- 마지막 날짜에 생성한 주문은 다음 시가 자료 없이는 체결하지 않는다. 순수익률은 평가손익과 발생 비용을 포함하고 강제 청산은 하지 않는다.",
              "", "## 의사결정 기록", "",
              "회사별 정책은 investment_replay_policy.json, 상세 근거와 공시 식별자는 decisions.jsonl.gz, 체결은 trades.csv.gz, 일별 잔고는 nav.csv.gz에 저장한다. 각 결정은 이전 기록의 해시와 연결된다. 재생 결과는 생성 시점의 연구 기록이며 역사적 실제 결정으로 위장하지 않는다.",
              "", "## 다음 전환 조건", "",
              "1. 날짜별 산업분류와 공식 지수, 상장폐지·기업행위·한국 배당 자료를 보완한다.",
              "2. 동결된 월별 AI 예측의 생성일·학습종료일을 검증하고 동일 조건의 AI 단독 비교군을 추가한다.",
              "3. 사전 근거가 있는 기업 논리 카드만 미래 모의운용에 연결하고 고정 전략과 별도로 평가한다.",
              "4. 팀 자금 재배분은 별도 실험으로 추가하고 비용·낙폭을 포함한 과거 성숙 성과만 사용한다.",
              "5. 실제 일일 원장과 무료 알림 가능 여부가 확인된 뒤 텔레그램 제안을 켠다. 이번 실행은 스케줄·알림·실주문을 생성하지 않는다.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=Path("data/reference/investment_replay_policy.json"))
    parser.add_argument("--out", type=Path, default=Path("docs/replays/2026-09-10-v1"))
    parser.add_argument("--markets", nargs="+", choices=["kr", "us"], default=["kr", "us"])
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit("Output exists; use a new --out directory to preserve the prior run")
    started = time.perf_counter()
    policy = json.loads(args.policy.read_text())
    if any(policy.get(k) for k in ["llm_api_calls", "paid_data_calls", "telegram_enabled", "daily_schedule_enabled", "capital_reallocation_enabled", "sector_limit_enabled"]):
        raise SystemExit("This runner supports the offline fixed-allocation pilot only")
    cohort_path = Path("data/reference/accounting_cohort.json")
    cohort = json.loads(cohort_path.read_text())
    args.out.mkdir(parents=True)
    inputs = {str(args.policy): sha256(args.policy), str(cohort_path): sha256(cohort_path)}
    bundle = {"policy": policy, "markets": {}}
    for market in args.markets:
        mark = time.perf_counter()
        prices_path = Path("data/raw/krx_ohlcv_kospi_kosdaq_state.csv" if market == "kr" else "data/raw/us_ohlcv_nasdaq_nyse_yfinfo_state.csv")
        filings_path = Path(f"data/dashboard_research/accounting/{market}/filing_events.csv.gz")
        prices = read_prices(prices_path, cohort["markets"][market], market)
        filings = pd.read_csv(filings_path, dtype={"ticker": str, "filing_id": str})
        if market == "kr":
            filings["ticker"] = filings.ticker.str.zfill(6)
        inputs[str(prices_path)], inputs[str(filings_path)] = sha256(prices_path), sha256(filings_path)
        data = prepare(prices, filings, policy, market)
        print(market, "prepared", data.audit, flush=True)
        result = replay(data, policy, market)
        stress = replay(data, policy, market, cost_multiplier=2.)
        target = args.out / market
        target.mkdir()
        content, tail = ledger(result["decisions"])
        write_gzip(target / "decisions.jsonl.gz", content)
        write_gzip(target / "trades.csv.gz", pd.DataFrame(result["trades"]).to_csv(index=False))
        write_gzip(target / "nav.csv.gz", pd.DataFrame({"date": result["dates"], **result["series"]}).to_csv(index=False))
        write_json(target / "research_requests.json", result["research_requests"])
        write_json(target / "unfilled_orders.json", result["unfilled_orders"])
        bundle["markets"][market] = {"summary": result["summary"], "stress_summary": stress["summary"],
                                     "audit": result["audit"], "decision_count": len(result["decisions"]),
                                     "trade_count": len(result["trades"]), "decision_ledger_tail": tail,
                                     "elapsed_seconds": round(time.perf_counter()-mark, 3)}
        print(market, "finished", round(time.perf_counter()-mark, 2), "seconds", flush=True)
    elapsed = time.perf_counter()-started
    bundle["elapsed_seconds"] = round(elapsed, 3)
    write_json(args.out / "summary.json", bundle)
    (args.out / "report.md").write_text(report(bundle, elapsed), encoding="utf-8")
    artifacts = {str(p.relative_to(args.out)): sha256(p) for p in sorted(args.out.rglob("*")) if p.is_file()}
    write_json(args.out / "manifest.json", {
        "research_created_at": pd.Timestamp.now(tz="UTC").isoformat(), "inputs": inputs,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "engine_sha256": sha256(Path("src/ai_stock_assistant/investment_replay.py")),
        "runner_sha256": sha256(Path(__file__)), "artifacts": artifacts,
        "llm_api_calls": 0, "paid_data_calls": 0, "scheduled_tasks_created": 0,
        "telegram_messages_sent": 0, "source_prices_ref": "dashboard-state",
        "python": sys.version, "pandas": pd.__version__,
    })
    print(args.out / "report.md", "total_seconds", round(elapsed, 2))


if __name__ == "__main__":
    main()
