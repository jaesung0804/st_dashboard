from ai_stock_assistant.investment_supervision import validate_proposal,review_promotion
from ai_stock_assistant.investment_strategy_briefs import BRIEF_FIELDS,strategy_proposal
import json
from pathlib import Path


def main():
    config=json.loads(Path('data/reference/employee_supervision_policy.json').read_text())
    valid=strategy_proposal('pulse_day','efficient',config,'2024-01-03')
    assert validate_proposal(valid)['approved']
    for key in BRIEF_FIELDS:
        assert not validate_proposal(valid|{'strategy_brief':valid['strategy_brief']|{key:' '}})['approved']
    assert not validate_proposal(valid|{'strategy_brief':valid['strategy_brief']|{'evidence_asof':'2024-01-04'}})['approved']
    assert not validate_proposal(valid|{'cost_bps':float('nan')})['approved']
    assert not validate_proposal({})['approved']
    for field,value,reason in [('inputs',['future_return'],'unapproved_or_future_information'),('gross_exposure',2,'leverage_outside_mandate'),('cost_bps',0,'cost_assumption_below_approved_floor'),('max_asset_weight',.8,'concentration_outside_team_budget'),('fill_delay',0,'same_session_execution')]:
        assert reason in validate_proposal(valid|{field:value})['reasons']
    evidence={'observations':252,'evidence_end':'2024-01-03','decision_date':'2024-01-03','cost2_net_return':.1,'max_drawdown':-.1}
    policy={'shadow_minimum_sessions':252,'supervisor_max_drawdown':.25}
    assert review_promotion(evidence,policy,valid)['approved']
    assert not review_promotion(evidence,policy)['approved']
    for field,value in [('observations',125),('evidence_end','2024-01-04'),('cost2_net_return',-.1),('max_drawdown',-.3)]:
        assert not review_promotion(evidence|{field:value},policy,valid)['approved']
    print('PASS mandatory strategy briefs, finite execution assumptions, promotion evidence clock and risk gates')


if __name__=='__main__':main()
