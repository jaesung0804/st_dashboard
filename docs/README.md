# 문서와 폴더 안내

처음에는 [최근 연결 점검](PROJECT_AUDIT_20260913.md)과 [웹 ChatGPT DB 연결 안내](CHATGPT_DB_ACCESS.md)를 읽습니다. 과거 보고서는 작성 당시의 상태를 보존하며, 현재 상태는 날짜가 명시된 새 점검을 기준으로 판단합니다.

## 운영과 복구

| 목적 | 문서 |
|---|---|
| 운영 모델·배치·예측 보존 | [monthly-ews.md](monthly-ews.md) |
| 화면 사용 | [dashboard-user-guide.md](dashboard-user-guide.md) |
| 한국 AUC·시장별 배치·미국 수집 복구 | [2026-09-11 시장 점검](MARKET_HEALTH_REVIEW_20260911.md) |
| 한국·미국 수집 장애 | [한국](krx_collection_recovery.md), [미국](us_collection_recovery.md) |
| 상태 저장 복구 | [state_upload_recovery.md](state_upload_recovery.md) |
| 게시 절차 | [GITHUB_SIMULATION_OPERATIONS.md](GITHUB_SIMULATION_OPERATIONS.md) |
| 검증 이력 | [validation/summary.md](validation/summary.md) |

## 연구와 회차

| 목적 | 문서 |
|---|---|
| 재무 연구 | [ACCOUNTING_RESEARCH.md](ACCOUNTING_RESEARCH.md) |
| 시뮬레이션 설계·입력 | [설계](INVESTMENT_REPLAY.md), [입력](INVESTMENT_REPLAY_INPUTS.md), [v2 수정](INVESTMENT_REPLAY_V2_CORRECTIONS.md) |
| 완료된 r03·89개 실험 | [회차 기록](INVESTMENT_REPLAY_SESSION_20260910.md), [r03](INVESTMENT_REPLAY_R03.md), [검토](INVESTMENT_REPLAY_REVIEW.md) |
| 조직·연구 논리 연결 | [운영](INVESTMENT_OPERATIONS.md), [연구실 연결](RESEARCH_LAB_INTEGRATION.md) |
| 후속 계획 | [다음 회차](INVESTMENT_NEXT_ROUND.md) |
| 미종료 r04·사용자 3차 | [복구 브랜치의 기록](https://github.com/jaesung0804/st_dashboard/blob/codex/investment-phase3-20260911/docs/INVESTMENT_PHASE3_RECOVERY.md) |

## 저장 위치

| 경로 | 역할·보존 기준 |
|---|---|
| `src/ai_stock_assistant/` | 모델·수집·연구 계산 라이브러리 |
| `scripts/` | 실행 명령과 빌더. 웹 자산은 `dashboard_web/`, `simulation_web/` |
| `.github/workflows/` | 예약·수동 배치와 검증 |
| `data/reference/` | 공개 정책·코호트·참조 문서. backend 모드의 운영 참조는 API에서 읽음 |
| `docs/replays/`, `docs/research_library/` | 이미 공개된 과거 실험·출처 증거. 경로와 원본 해시 보존 |
| `data/raw/`, `data/processed/` | 로컬 원자료·처리 자료. Git 제외, 임의 삭제 금지 |
| `outputs/` | 로컬 결과·대시보드. 이름에 `latest`가 있어도 실제 manifest 날짜 확인 |
| `.research-backend/` | 백엔드 복원 영수증·아티팩트 캐시. Git 제외 |
| `.dashboard-state/` | 상태 복원·저장 작업 공간. 현재 backend 원본은 Oracle/OCI |
| `.pages-deploy/`, `.simulation-deploy/` | 재생성 가능한 게시·미리보기 결과. Git 제외 |
| `.work/` | 임시 실행 환경·진단. Git 제외 |
| `.archive/YYYY-MM-DD/` | 퇴역한 로컬 결과의 복구용 보관. 원래 위치는 `manifest.json`에 기록 |
| `.venv/` | Python 실행 환경. 이동하면 절대경로가 깨질 수 있어 위치 유지 |

코드·정책·작은 점검 요약은 Git, 운영 기록·조회용 행은 DB, 원자료·모델·큰 결과는 비공개 파일 저장소에 둡니다. 파일 정리 시에는 코드 참조와 Git 상태를 먼저 확인합니다. 보관 폴더는 저장 공간을 줄이는 압축이나 삭제와 다릅니다.

Windows의 연구 명령은 UTF-8 모드(`python -X utf8 ...`)를 권장합니다. 운영 환경 의존성은 `requirements-live.txt`, 연구 환경은 `requirements.txt`를 따릅니다. 단순 점검·조회 과정에서 수집이나 학습을 실행하지 않습니다.
