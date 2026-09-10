"""Explicit management gates for research staff; no free-form agent can bypass them."""
from __future__ import annotations
ALLOWED_INPUTS={'trailing_price','trailing_volume','filing_asof','dated_shadow_returns'}


def validate_proposal(proposal):
    reasons=[]
    if set(proposal.get('inputs',[]))-ALLOWED_INPUTS:reasons.append('unapproved_or_future_information')
    if proposal.get('gross_exposure',1)>1 or proposal.get('gross_exposure',1)<0:reasons.append('leverage_outside_mandate')
    if proposal.get('max_asset_weight',.1)>.1:reasons.append('concentration_outside_team_budget')
    if proposal.get('cost_bps',10)<10:reasons.append('cost_assumption_below_approved_floor')
    if proposal.get('fill_delay',1)<1:reasons.append('same_session_execution')
    return {'approved':not reasons,'reasons':reasons}


def review_promotion(evidence,policy):
    """Evidence is explicitly dated; senior staff cannot inspect a future row."""
    reasons=[];advice=[]
    if evidence['observations']<policy['shadow_minimum_sessions']:reasons.append('insufficient_post_birth_observation')
    if evidence['evidence_end']>evidence['decision_date']:reasons.append('future_evidence')
    if evidence['cost2_net_return']<=0:
        reasons.append('fails_double_cost_profitability');advice.append('Reduce turnover or extend the review interval before requesting more capital.')
    if evidence['max_drawdown'] < -policy.get('supervisor_max_drawdown',.25):
        reasons.append('drawdown_above_team_risk_budget');advice.append('Test smaller exposure and a trend guard in the research account.')
    return {'approved':not reasons,'reasons':reasons,'advice':advice}
