# 월 학습 · 일 추론 조기경보 모델

2026-09 운영 경로는 `monthly_ews.py`이다. 기존 `run_walkforward_warning*.py`는 연구용으로 남겨 두며 일 배치에서 호출하지 않는다.

## 운영 계약

| 작업 | 실행 시점 | 저장/변경 대상 |
|---|---|---|
| Monthly Training | 매월 1일 00:30 UTC (09:30 KST), 또는 수동 실행 | 해당 월 모델이 없을 때만 학습 |
| Daily Refresh | 평일 22:30 UTC (다음 날 07:30 KST) | 최신 완료 신호일이 없을 때만 추론 |
| 실현 결과 평가 | 월 배치 | 별도 월별 평가 스냅샷 |
| Pages | 일 배치의 상태 push 성공 후 | 최근 22신호일의 정적 HTML/JSON |

첫 도입 커밋은 월 배치를 한 번 시작한다. 월 배치 성공 후 일 배치가 자동 실행된다. 월 배치와 일 배치는 같은 `dashboard-state-update` 동시 실행 그룹을 공유한다. GitHub의 예약 실행 시각은 지연될 수 있다.

월 모델은 이전 달 마지막 날까지의 데이터만 읽는다. 현재 월 이후/이전 월 모델을 운영 모드로 새로 만들 수 없다. 재실행은 기존 모델의 체크섬을 검증하고 그대로 사용한다. 일 배치에는 학습 호출이 없으며 필요한 월 모델이 없으면 실패한다. 임의의 이전 월 모델로 조용히 대체하지 않는다.

예측은 `data/dashboard_ews/<kr|us>/predictions/YYYY-MM-DD/{rows.json,meta.json}`에 한 번 저장한다. 같은 신호일을 다시 실행해도 모델 변경, 원천 가격 정정, 종목명 변경으로 덮어쓰지 않는다. 과거 날짜에 새 점수를 채워 넣지도 않는다. 새 예측의 생성 시각이 신호일보다 늦으면 `delayed`로 표시한다. 이는 소급한 실시간 예측이 아니며 종가에 실제 매매할 수 있었다는 뜻도 아니다.

운영 장애로 날짜 사이가 비었을 때는 `Backfill recommendation history` 작업이 원시 가격에 실제로 존재하는 거래일만 확인한다. 기존 live/delayed/legacy 기록은 그대로 두고 누락일만 `data/dashboard_research/reconstruction`에 `reconstructed`로 저장한다. 각 날짜보다 앞선 월말까지만 학습한 고정 모델을 사용하며, 최신 정상 운영 예측을 같은 배치로 재현하지 못하면 저장·게시를 중단한다. 따라서 이 자료는 날짜 선택에는 나타나지만 당시 생성된 실시간 예측이나 실전 성과로 집계하지 않는다.

모델은 `models/YYYY-MM/{model.json,model.sha256,up.txt,down.txt}`에 저장한다. 입력 스키마, 학습 기준일, 라벨 만기, 학습·보정 구간, 난수 시드, 패키지 버전, 코드 커밋, 입력 CSV 체크섬이 기록된다. 새 모델/예측 디렉터리는 완성 후 원자적으로 이동하며 기존 디렉터리를 대체하지 않는다.

기존 Pages에 남아 있는 예측 JSON은 `legacy/`에 원래 바이트 그대로 보존한다. 이미 과거 배치가 덮어쓴 기록까지 복원할 수는 없다. 이 기록은 버전 없는 기존 모델의 **순위 점수**이며 새 모델의 사건 확률과 직접 비교하면 안 된다. 이전 모델로 계산된 예상 주가는 새 화면에서 표시하지 않는다.

## 모델의 의미

| 경보 | 사건 정의 | 관측 기간 |
|---|---|---|
| 상승 기회 | 조정 종가 수익률 +20% 이상이면서 신호일 유동성 조건을 통과한 종목들의 중간 수익률보다 +10%p 이상 | 이후 126개 종목 거래 관측일 |
| 급락 위험 | 신호 종가 대비 이후 조정 종가가 한 번이라도 -20% 이하 | 이후 63개 종목 거래 관측일 |

두 사건은 동시에 발생할 수 있다. 확률을 합쳐 100%가 되는 분류가 아니다. 상승 시점/목표 주가를 맞히는 회귀 모델도 아니다. 시장 전체가 하락하면 다수 종목에 동시에 경보가 날 수 있도록 하위 5% 강제 할당을 없앴다.

시장별로 정규화 로지스틱 회귀와 작은 LightGBM을 각각 학습하여 50:50으로 합친다. LightGBM은 경보당 180개, 최대 15잎의 트리이며 한 번에 최대 180,000개 주간 표본을 학습한다. 클래스 가중치를 사용하지 않는다. 과거 입력으로부터 계산한 확률에 시간 순서가 분리된 보정 구간의 sigmoid 보정을 50% 반영한다. 최근 한 국면의 사건 빈도가 장기 추정을 전부 대체하지 않도록 한 것이다. 이 선택은 아래 개발 검증에서 전체 보정의 확률 왜곡을 확인한 뒤 적용했다.

입력은 25개의 가격·거래량·상대강도·시장 폭·변동성 지표다. 시장 지표도 각 시장에서 이미 완료된 종가만 쓴다. 최신 재무자료에 임의의 발표일을 붙이는 방식, 수정된 최신 거시 시계열, 현재 발행주식 수로 만든 과거 시가총액은 제외했다. 기존 수집 재무자료는 상태에 남지만 운영 모델과 일 배치에서 갱신/사용하지 않는다.

기본 유동성 조건은 20일 평균 거래대금 한국 5억원/미국 500만 달러, 종가 한국 1,000원/미국 1달러, 최소 253개 관측일 및 당일 거래량이다. 상장 당시부터 완전한 역사적 종목 구성을 복구한 것은 아니다.

등급은 확률 35% 이상 RED, 20% 이상 ORANGE, 10% 이상 YELLOW, 그 아래 GREEN이다. 이 경계는 수익 최적화한 매매 규칙이 아니라 고정된 화면 표시 기준이다. 관심 후보는 상승 확률 20% 이상이고 보정 전후 하락 위험 추정의 **높은 값**이 15% 미만인 종목이다. 화면의 위험 범위는 보정 전후 추정 차이이며 통계적 신뢰구간이 아니다. 위험 기여 설명은 트리 부분의 연관성이고 원인이나 전체 혼합 모델의 완전한 설명이 아니다.

## 시간 순서와 평가

미래 라벨이 전부 관측되지 않은 표본은 음성(0)으로 채우지 않는다. 각 경보의 라벨 만기일이 학습 기준일 이전이어야 한다. 마지막 13개 주간 신호일을 보정에 쓰고 학습 표본의 **라벨 만기일**이 보정 첫 신호일에 닿으면 제외한다. 결측치 대체와 정규화도 학습 표본에만 맞춘다. 패널의 행 번호를 무작위 분할하지 않는다. [시계열 분할 원칙](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html), [전처리 누출 방지](https://scikit-learn.org/stable/common_pitfalls.html), [확률 보정과 분리된 보정 자료](https://scikit-learn.org/stable/modules/calibration.html)를 참고했다.

`calibration_diagnostics_not_test`는 보정에 사용된 데이터의 진단 결과이지 독립 성능이 아니다. `audit_monthly_ews.py`는 해당 월 이전 데이터로 동결한 모델을 이후 월의 완전히 만기가 지난 표본으로 평가한다. `docs/validation/`에 2025-03/2025-12 한국·미국 개발 검증을 보관한다. 두 기간을 모델 설계 점검에 사용했으므로 최종 모델 선택과 독립적인 새로운 실전 성과로 주장하지 않는다.

평가지표는 ROC AUC, PR AUC(average precision), Brier 점수, 상위 위험/기회 10%의 사건 발생률과 배수다. 수수료·실행 지연을 넣은 전략 수익률이나 독립 시행 횟수가 아니다. 같은 종목과 겹치는 63/126일 구간은 서로 상관되어 있다. 해당 기간의 단순 학습 사건 빈도 및 로지스틱/트리 단독 추정도 함께 기록한다.

실전 예측의 만기가 도래하면 월 배치가 원본 예측을 수정하지 않고 `evaluations/YYYY-MM.json`에 평가한다. 평가 가격의 데이터 버전(체크섬)을 남긴다. 정지·상장폐지·미관측 결과는 음성으로 바꾸지 않고 결측 수와 평가 가능 비율로 보고한다. 원천 데이터의 생존 편향, 소급 가격 정정, 불완전한 기업행사 조정은 남아 있다. 새 데이터에서도 같은 구분력이나 확률 정확도가 유지된다는 보장은 없다.

## 재현과 복구

```powershell
python -m pip install -r requirements-live.txt
python -m pip install -e .
python scripts/unpack_dashboard_state.py --state-dir .dashboard-state
python scripts/run_monthly_ews.py migrate --legacy-pages .legacy-pages
python scripts/refresh_dashboard_pipeline.py --collect-only --market all
python scripts/run_monthly_ews.py train --market all --jobs 1
python scripts/refresh_dashboard_pipeline.py --market all --skip-push
python scripts/validate_live_dashboard.py --market all
python scripts/pack_dashboard_state.py --state-dir .dashboard-state
python scripts/push_dashboard_state.py --state-dir .dashboard-state
python scripts/build_pages_deploy.py --days 22 --push
```

연구 모드는 반드시 별도 상태 디렉터리를 쓴다:

```powershell
python scripts/run_monthly_ews.py train --month 2025-12 --market kr --research --state-root outputs/research-state
python scripts/audit_monthly_ews.py --months 2025-12 --market kr --state-root outputs/research-state --output outputs/audit-kr.json
```

새 모델 출시 때는 입력 버전과 월 경계를 함께 관리한다. 운영 중인 월 모델/예측을 삭제하거나 강제 덮어쓰지 않는다. 월 배치가 실패하면 원인을 고치고 같은 배치를 재실행한다. 한 시장만 모델이 준비된 경우 전체 월 배치를 다시 실행하면 완성된 쪽은 그대로 두고 나머지만 만든다.

상태 push가 실패하면 Pages도 공개하지 않으며 배치는 실패로 끝난다. 해당 실행의 7일 보관 복구 artifact를 사용할 수 있다. 재시도는 force-push나 상태 재생성을 하지 않는다. 저장된 예측과 모델 파일은 `data/dashboard_ews/** -text`로 Windows 개행 변환을 차단한다. 기존 분할 CSV의 무손실 복구도 그대로 유지한다.
