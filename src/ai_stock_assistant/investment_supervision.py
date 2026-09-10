"""Explicit management gates for research staff; no free-form agent can bypass them."""
from __future__ import annotations
from datetime import date
from math import isfinite
from .investment_strategy_briefs import BRIEF_FIELDS
ALLOWED_INPUTS={'trailing_price','trailing_volume','filing_asof','dated_shadow_returns'}


def validate_proposal(proposal):
    reasons=[]
    brief=proposal.get('strategy_brief')
    if not isinstance(brief,dict) or any(not isinstance(brief.get(k),str) or not brief[k].strip() for k in BRIEF_FIELDS):
        reasons.append('strategy_brief_incomplete')
    else:
        try:
            if date.fromisoformat(brief['evidence_asof'])>date.fromisoformat(proposal['decision_date']):
                reasons.append('future_strategy_evidence')
        except (KeyError,TypeError,ValueError):reasons.append('strategy_evidence_date_invalid')
        interval=brief.get('review_interval_sessions')
        if not isinstance(interval,int) or isinstance(interval,bool) or interval<1:reasons.append('strategy_horizon_invalid')
    inputs=proposal.get('inputs')
    if not isinstance(inputs,list) or not inputs or any(not isinstance(i,str) or i not in ALLOWED_INPUTS for i in inputs):
        reasons.append('unapproved_or_future_information')
    for key,lower,upper,reason in [('gross_exposure',0,1,'leverage_outside_mandate'),
                                 ('max_asset_weight',0,.1,'concentration_outside_team_budget'),
                                 ('cost_bps',10,float('inf'),'cost_assumption_below_approved_floor'),
                                 ('fill_delay',1,float('inf'),'same_session_execution')]:
        value=proposal.get(key)
        if not isinstance(value,(float,int)) or isinstance(value,bool) or not isfinite(value) or not lower<=value<=upper:
            reasons.append(reason)
    return {'approved':not reasons,'reasons':reasons}


def review_promotion(evidence,policy,proposal=None):
    """Evidence is explicitly dated; senior staff cannot inspect a future row."""
    proposal_check=validate_proposal(proposal or {})
    reasons=list(proposal_check['reasons']);advice=[]
    if not proposal_check['approved']:advice.append('Complete the strategy brief and permitted execution assumptions before requesting approval.')
    if proposal and proposal.get('decision_date')!=evidence['decision_date']:reasons.append('proposal_review_date_mismatch')
    if any(not isinstance(evidence.get(k),(int,float)) or isinstance(evidence.get(k),bool) or not isfinite(evidence[k])
           for k in ('observations','cost2_net_return','max_drawdown')):
        return {'approved':False,'reasons':reasons+['invalid_promotion_evidence'],'advice':advice}
    if evidence['observations']<policy['shadow_minimum_sessions']:reasons.append('insufficient_post_birth_observation')
    if evidence['evidence_end']>evidence['decision_date']:reasons.append('future_evidence')
    if evidence['cost2_net_return']<=0:
        reasons.append('fails_double_cost_profitability');advice.append('Reduce turnover or extend the review interval before requesting more capital.')
    if evidence['max_drawdown'] < -policy.get('supervisor_max_drawdown',.25):
        reasons.append('drawdown_above_team_risk_budget');advice.append('Test smaller exposure and a trend guard in the research account.')
    return {'approved':not reasons,'reasons':reasons,'advice':advice}
