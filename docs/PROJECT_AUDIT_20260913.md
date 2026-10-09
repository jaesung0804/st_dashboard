# 주식 프로젝트 연결 점검 — 2026-09-13

대상: [st_dashboard](https://github.com/jaesung0804/st_dashboard), 로컬 `C:/stock`. 확인한 원격 main은 `2d7c2df`입니다. 이 문서는 해당 시점의 점검이며 자동 갱신 상태판은 아닙니다.

## 앞선 작업과 연결

| 항목 | 확인 결과 |
|---|---|
| 로컬·GitHub | 얕은 Git 이력 때문에 처음에는 분기된 것처럼 표시됨. main 이력을 추가로 받아 조상 관계를 확인하니 로컬은 22개 커밋 뒤처져 있었음. 백업 브랜치 후 fast-forward 완료 |
| DB 이관 | [PR #1](https://github.com/jaesung0804/st_dashboard/pull/1) main 반영. 현재 HTTPS API `ready`: Oracle/OCI |
| 한국 AUC·한미 배치 분리 | [PR #2](https://github.com/jaesung0804/st_dashboard/pull/2) 반영 |
| Windows 결과 해시·저장 순서 | [PR #3](https://github.com/jaesung0804/st_dashboard/pull/3) 반영 |
| 공유 상태 대기열 | [PR #4](https://github.com/jaesung0804/st_dashboard/pull/4) 반영 |
| 미종료 사용자 3차/r04 | `codex/investment-phase3-20260911`에 main에 없는 6개 커밋·4개 파일. 9개 대조 계산 보존은 기록되어 있으나 정식 종료는 보류 |

기존 main 백업: `codex/backup-before-cleanup-20260913` (`faf1299`). 다른 로컬 worktree와 원격 연구 브랜치·Git 이력은 보존했습니다.

## DB 현재 조회 결과

2026-09-13에 프로젝트 API의 게시된 dataset metadata를 조회해 합산했습니다.

| 시장 | 게시 월 수 | 게시 행 수 | 월 범위 | 최신 월 샘플 조회 |
|---|---:|---:|---|---|
| 한국 | 65 | 3,147,009 | 2021-05~2026-09 | 성공 |
| 미국 | 64 | 5,923,741 | 2021-06~2026-09 | 성공 |
| 합계 | 129 | 9,070,750 | | |

현재 `pipeline-state` head는 `3f16bd15917e459bbaa680e17bf08976`, 갱신 시각은 2026-09-11 14:57:05 UTC입니다. 게시된 SQL 버전의 마지막 갱신은 한국 06:52:45 UTC, 미국 15:43:57 UTC입니다.

따라서 과거 이관 보고서의 “미국 과거 SQL 적재 중단”은 현재 게시 상태를 설명하지 못합니다. 이번 검사는 전체 가격행 재검산·스냅샷과 SQL의 행별 일치 검증·총 저장 공간 검사까지 수행한 것은 아닙니다. 월별 metadata와 최신 월 실제 조회가 확인된 범위입니다.

DB의 회차 목록에는 r03 한 개가 조회됐습니다. r04 완료 등록은 확인되지 않았으며 [복구 문서](https://github.com/jaesung0804/st_dashboard/blob/codex/investment-phase3-20260911/docs/INVESTMENT_PHASE3_RECOVERY.md)도 신규 역할 등록·별도 검토·체크포인트 동기화·정식 회차 종료를 미완료로 명시합니다. 해당 결과를 완료된 89개 실험이나 승인된 운영 배정에 합치지 않았습니다.

## 자동운영과 공개 결과

- [최근 미국 예약 배치](https://github.com/jaesung0804/st_dashboard/actions/runs/34610698509): 9월 11일 성공.
- [최근 Pages 검증](https://github.com/jaesung0804/st_dashboard/actions/runs/34613670026)과 [실제 배포](https://github.com/jaesung0804/st_dashboard/actions/runs/34613685024): 성공.
- 공개 manifest를 직접 조회한 최신 신호일: 한국·미국 모두 **2026-09-10**. 포함 날짜는 한국 47개·미국 46개.
- 새 `Daily Refresh KR` 예약 실행 이력은 아직 없음. 금요일 한국 예약시각 이후 도입되어 다음 평일 예약을 아직 통과하지 않은 상태. 앞선 [수동 복구의 양 시장 성공](https://github.com/jaesung0804/st_dashboard/actions/runs/34567918631)과 별개로 판단.

미국 공개 기준일은 9월 10일이므로 9월 11일 종가까지 게시됐다고 표현하지 않습니다. 배포 성공과 데이터 최신성은 다릅니다. 한국 AUC 새 성능 검증, 휴장·특별 개장 시각 처리, SQL을 활용한 증분 계산 전환은 여전히 후속 과제입니다.

## 폴더 정리와 검증

문서 진입점과 DB 연결 안내를 추가하고, 기존 연구·운영 문서와 미종료 브랜치의 위치를 연결했습니다. 해시로 참조되는 `docs/replays/`와 `data/reference/`의 경로·내용은 유지했습니다.

6월의 퇴역한 중간 특징 CSV 2개는 합계 **14,723,818,716바이트**입니다. 현재 코드에서 해당 디렉터리 참조가 없는 것을 확인하고, 오래된 연구 결과·배포 및 상태 체크아웃과 함께 `.archive/2026-09-13/`로 이동했습니다. 원래 위치와 파일별 크기는 그 안의 `manifest.json`에 남깁니다. 압축·삭제로 저장 공간을 확보한 작업은 아닙니다. 현재 로컬 `data/`와 `outputs/...latest`는 생성 시점의 캐시이므로, 운영 작업 전 최신 backend 상태를 복원해야 합니다.

초기 Windows 전체 검사에서 UTF-8 정책 JSON을 기본 CP949로 읽어 9개 테스트가 실패했습니다. 실패한 테스트 입력과 실제 시뮬레이션 빌더의 텍스트 I/O에 UTF-8을 명시했습니다. 모델·실험 원본 바이트는 수정하지 않았습니다.

최종 검증:

- Windows 기본 인코딩에서 전체 Python 테스트 **181개 통과**. 기존 pandas 경고 6개는 남아 있음.
- JavaScript UI 검사 통과.
- `python -X utf8=0 scripts/build_simulation_pages.py --out .work/audit-20260913/simulation`: 89개 실험 빌드 성공. 생성 HTML과 JSON을 UTF-8로 재확인.
- 새 문서·README의 로컬 링크, Git diff 공백 검사 통과.
- 7개 경로·9,765개 파일·16,124,572,673바이트를 보관 위치에서 크기 대조. 보관 자료·원자료·진단 캐시는 Git에서 제외.

이번 작업은 운영 모델 재학습·전체 재수집·DB 쓰기·Pages 재게시를 실행하지 않았습니다. 웹 ChatGPT용 MCP 연결은 별도 구현·등록 과제로 남습니다.
