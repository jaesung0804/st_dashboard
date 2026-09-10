"""Human-readable explanations of the implemented rule proxies, not LLM theses."""
from __future__ import annotations

from .investment_replay import DESKS

BRIEF_FIELDS = ('thesis', 'entry', 'exit', 'horizon', 'risk', 'evidence_asof')
THESES = {
    'pulse_day': '최근 5일·20일 수익률 순위를 반씩 반영해 단기 상대강도 지속을 시험합니다.',
    'pulse_week': '최근 20일 수익률 순위가 높은 종목의 추세 지속을 시험합니다.',
    'pulse_month': '최근 63일 수익률 순위가 높은 종목의 중기 추세 지속을 시험합니다.',
    'compound_quarter': '공시로 확인된 매출 성장과 자산수익성·현금흐름·낮은 발생액·부채를 함께 평가합니다.',
    'compound_half': '수익성과 영업현금흐름, 설비투자 후 현금흐름, 현금 보유가 좋은 기업을 선호합니다.',
    'compound_year': '자산수익성과 낮은 부채를 중시하고 현금흐름·발생액으로 재무 지속성을 점검합니다.',
    'adaptive_tactical': '20일 상대강도와 낮은 20일 변동성 순위를 반씩 반영합니다.',
    'adaptive_quality': '공시 기반 수익성·현금흐름·발생액·부채와 63일 상대강도를 함께 평가합니다.',
    'adaptive_defensive': '최근 20일 변동성이 낮은 종목을 선호하고 시장 폭이 약하면 투자 비중을 줄입니다.',
}


def strategy_brief(team, genome, policy, evidence_asof):
    if team not in THESES or genome not in {'original', 'efficient', 'guarded', 'moderate'}:
        raise ValueError('Unknown strategy identity')
    interval = DESKS[team][0] if genome == 'original' else policy['efficient_intervals'].get(team, DESKS[team][0])
    interval = policy.get('desk_interval_overrides', {}).get(team, interval)
    buffer = 15 if genome == 'moderate' else policy['rank_buffer']
    budget = .35 if genome == 'moderate' else policy['volatility_budget']
    thesis = THESES[team]
    entry = '126거래일 연속 유효 가격과 20일 평균 거래대금 기준을 충족한 후보 중 점수 상위 최대 10종목을 고릅니다.'
    if team.startswith('pulse') or team == 'adaptive_tactical':
        entry += ' 60일 평균가격 위에 있는 종목만 신규 후보로 삼습니다.'
    if genome in {'guarded', 'moderate'}:
        entry += ' 200일 평균가격 위라는 조건도 적용합니다.'
        if team.startswith('compound') or team == 'adaptive_quality':
            thesis += ' 기본 점수 75%에 126일 상대강도 25%를 더합니다.'
    exit_rule = '정기 검토에서 자격 또는 선택 순위를 잃으면 다음 거래일 시가에 축소·매도를 시도합니다.'
    if genome != 'original':
        exit_rule += f' 기존 보유는 상위 {buffer}위 안에서 우선 유지하고, 계속 보유할 종목의 {policy["no_trade_band"]:.1%} NAV 미만 변경은 생략합니다.'
    exit_rule += ' 거래 불가능한 보유분은 임의로 삭제하지 않습니다.'
    risk = f'무차입·공매도 없음, 종목당 팀 자산 {policy["max_position_weight_per_team"]:.0%} 목표 상한, 회사 전체 전일 거래량 {policy["max_previous_day_volume_fraction"]:.0%} 이내 체결.'
    if genome in {'guarded', 'moderate'}:
        risk += f' 선택 종목 평균 변동성 대비 {budget:.0%} 예산으로 투자 비중을 낮춥니다.'
    if team.startswith('adaptive') or genome in {'guarded', 'moderate'}:
        risk += ' 상승 추세 후보 비율이 45% 미만이면 투자 비중을 최대 50%로 낮춥니다.'
    risk += f' 편도 비용 {policy["all_in_cost_bps"]["us"]}bp. 낙폭 {policy.get("supervisor_max_drawdown", .25):.0%}는 승진 검토 기준이며 일중 손절 보장선은 없습니다.'
    return dict(thesis=thesis, entry=entry, exit=exit_rule,
                horizon=f'{interval}거래일마다 정기 검토하며 조건이 유지되면 더 오래 보유합니다. 직원 배정 변경 시 별도 재검토합니다.',
                review_interval_sessions=interval, risk=risk, evidence_asof=str(evidence_asof),
                execution_note='직원 시험과 별도로 실제 배정 계좌는 공통 효율형 매매 주기·거래 생략 기준을 사용합니다.',
                data_note='해당 신호일 종가까지의 가격·거래량 및 접수 다음 날 이후 이용 가능한 공시만 사용합니다.')


def strategy_proposal(team, genome, policy, evidence_asof):
    return dict(team=team, genome=genome, decision_date=str(evidence_asof),
                inputs=['trailing_price', 'trailing_volume', 'filing_asof', 'dated_shadow_returns'],
                gross_exposure=1., max_asset_weight=policy['max_position_weight_per_team'],
                cost_bps=policy['all_in_cost_bps']['us'], fill_delay=1,
                strategy_brief=strategy_brief(team, genome, policy, evidence_asof))


def brief_markdown(brief):
    labels = [('thesis', '투자 논리'), ('entry', '매수 조건'), ('exit', '축소·매도'),
              ('horizon', '주기·보유'), ('risk', '위험·비용'), ('evidence_asof', '근거 기준일')]
    return '\n'.join(f'- {label}: {brief[key]}' for key, label in labels)
