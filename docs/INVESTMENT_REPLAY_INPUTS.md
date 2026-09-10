# 재실행 입력과 캐시 출처 검증

V2 실행기는 이제 원본 가격 파일, 코호트 파일, 선별 함수·pandas 버전, 캐시 내용의 실제 해시를 함께 검사한다. 기존처럼 캐시 파일이 있다는 이유만으로 사용하거나 원본 SHA를 코드에 고정해 기록하지 않는다. 이미 완료된 실험의 결과·manifest는 수정하지 않았다.

## 재사용과 재선별

`investment_replay_inputs.load_retained_prices`는 가격 캐시 옆에 `*.pkl.metadata.json`을 저장한다. 메타데이터에는 캐시 버전, 실제 원본 SHA, 코호트 파일과 선택 종목 집합의 SHA, 읽기 함수의 구현 SHA, pandas 버전, 캐시 SHA, 행·종목 수, 날짜 범위, 열과 자료형을 기록한다.

- 원본이 있으면 매번 실제 SHA를 확인한다. 원본·코호트·캐시·읽기 규칙이 모두 같으면 검증된 캐시를 사용한다.
- 원본이나 코호트가 달라졌거나 메타데이터가 없거나 캐시가 변경됐다면 원본에서 다시 선별한다. 선별 중 원본 또는 코호트가 바뀌면 중단한다.
- 캐시는 검사한 동일 바이트를 읽는다. 다시 만든 캐시 바이트가 기존과 같으면 기존 파일과 수정 시각을 보존한다. 교체가 필요할 때는 임시 파일 작성 후 원자적으로 교체한다.
- 요청한 종목이 원본에서 누락되거나 중복 관측이 있으면 중단한다. 임의로 종목을 제거하지 않는다. 캐시 경로로 원본·코호트를 덮어쓰는 것도 거부한다.
- 원본이 없으면 기본적으로 중단한다. `--allow-verified-offline-cache`를 명시했을 때만 코호트·버전·읽기 규칙·캐시 SHA가 모두 검증된 로컬 캐시를 사용한다. 이때 `mode=verified_cache_without_source`, `source_revalidated_this_run=false`로 표시하고 원본 해시는 과거 확인한 스냅샷의 값임을 구분한다.

해시는 입력 변경을 탐지하는 재현성 기록이며 외부 출처의 진위를 인증하는 서명이 아니다. pickle은 기존에 신뢰하는 로컬 작업 산출물만 사용한다.

## V2 산출물

새 실행에는 `input_provenance.json`이 추가된다. 새 manifest의 `retained_prices`, `retained_cache`, `long_cache`, 코호트·공시·SPY 해시는 실제 사용한 입력에서 계산한다. 공시·SPY 및 개별 장기 가격·검증 자료는 해시를 계산한 동일 바이트를 파싱한다.

장기 패널은 매 실행마다 개별 원본과 보존 가격에서 다시 만들고, 사용한 개별 가격 SHA, 검증 JSON SHA 및 보존 가격의 출처를 기록한다. `us_replay_long_cache.pkl.metadata.json`에는 그 의존 입력 목록을 남긴다. 가격 이력이 줄어든 등의 사유로 보존 스냅샷을 선택하는 기존 규칙은 유지하며 서로 다른 조정 가격 기준을 이어 붙이지 않는다.

이 개선이 적용된 실행기는 `run_investment_replay_v2.py`다. 기존의 다른 실험 스크립트가 직접 `pd.read_pickle`로 읽는 동작까지 바꾸지는 않았다. 앞으로 해당 스크립트를 재실행할 때도 이 검증 함수를 연결하거나 원본·캐시 출처를 별도 검증해야 한다. 역사적 생존 편향과 상장폐지 자료 부족을 해결한 변경은 아니다.

## 사용법

```bash
python scripts/run_investment_replay_v2.py --source /path/to/research-parallel --out docs/replays/new-verified-run
python scripts/run_investment_replay_v2.py --source /path/to/research-parallel --retained-source /path/to/prices.csv --cohort data/reference/accounting_cohort.json --retained-cache data/raw/us_replay_cohort_cache.pkl --out docs/replays/another-new-run
PYTHONPATH=src python tests/test_investment_replay_inputs.py
```

`--out`은 새 폴더여야 한다. 원본을 복원할 수 없어 검증 캐시를 사용할 경우에만 `--allow-verified-offline-cache`를 추가한다. 이는 보존 가격 원본에 관한 옵션이며 장기 개별 가격, SPY, 공시 자료까지 생략하는 옵션은 아니다. 캐시와 sidecar는 로컬 `data/raw`에 두며 공개 업로드 대상이 아니다.

## 현재 자료 확인

2026-09-10 실제 업무시간에 원본에서 다시 선별한 보존 패널과 장기 패널이 각각 기존 캐시와 바이트 단위로 같음을 확인했다. 두 캐시의 수정 시각도 보존했다. 로컬 sidecar만 추가했다.

| 입력 | 확인 내용 |
| --- | --- |
| 보존 원본 | SHA `d20459d3205b7694d2e77068bd7e1d7e329c970e3c0d2f46c17f0b334110d94a` |
| 보존 캐시 | 128종목, 169,196행, 2021-06-01–2026-09-08 |
| 보존 캐시 SHA | `b82e69df5e9fd379e54ef5b9a45bcb9a55b52417c395ac3c34ccd2bb609480db` |
| 장기 캐시 | 128종목, 519,972행, 2006-01-03–2026-09-09; 장기 원본 123개와 보존 스냅샷 5개 |
| 장기 캐시 SHA | `cea954743c2a40d998141d4f03fa06aa53a6ffcba5be7abbc7f987e30ea8fa7e` |

원본 변경, 코호트 변경, 캐시 변조, 원본 없는 경우의 명시적 사용, 메타데이터·버전 변경, 파생 입력 기록, 원본 덮어쓰기 방지를 다루는 7개 테스트가 통과했다. 새 수익률 실험은 이 작업에서 추가하지 않았다.
