'use strict';
for (const market of ['kr', 'us']) {
  const card = document.getElementById(`market-${market}`);
  const put = (name, value) => { card.querySelector(`[data-${name}]`).textContent = value; };
  EWS.json(`lgbm_warning_dashboard_macro_${market}_latest/manifest.json`).then(manifest => {
    const freshness = EWS.freshness(manifest.latest);
    put('date', EWS.dateText(manifest.latest));
    put('status', freshness.label);
    card.querySelector('[data-status]').classList.toggle('stale', freshness.stale);
    put('health', freshness.description);
    put('scored', EWS.number(manifest.latestRows));
    put('up', EWS.number(manifest.latestUp));
    put('final', EWS.number(manifest.latestFinal));
    const model = manifest.models?.[String(manifest.latest).slice(0,7)];
    put('model', model ? `${model.month} 모델 · 학습 기준 ${EWS.dateText(model.cutoff)}` : '모델 설명에서 학습 기준 확인');
  }).catch(() => {
    put('status', '조회 오류');
    put('health', '기준일을 불러오지 못했습니다. 종목 탐색 화면에서 다시 확인해 주세요.');
    put('model', '모델 정보 확인 필요');
  });
}
