"""Prevent current company narratives from inheriting unrelated backtest proof."""
from __future__ import annotations
from datetime import datetime,timezone


def _time(value):
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:raise ValueError('Research clocks require an explicit timezone')
    return parsed.astimezone(timezone.utc)


def research_usage_gate(hypothesis,decision_at):
    reasons=[]
    if hypothesis.get('executable') is not True:reasons.append('research_only_not_executable')
    try:
        known=_time(hypothesis['known_at']);decision=_time(decision_at)
        if known>decision:reasons.append('hypothesis_not_known_at_decision')
        for source in hypothesis.get('decision_inputs',[]):
            if _time(source['available_at'])>decision:reasons.append('future_decision_input')
    except (KeyError,ValueError,TypeError):reasons.append('unknown_research_clock')
    if not hypothesis.get('decision_inputs'):reasons.append('missing_dated_company_inputs')
    if not hypothesis.get('rule_version') or not hypothesis.get('thresholds_registered_at'):
        reasons.append('rule_or_thresholds_unregistered')
    else:
        try:
            if _time(hypothesis['thresholds_registered_at'])>_time(decision_at):reasons.append('thresholds_registered_after_decision')
        except (ValueError,TypeError):reasons.append('unknown_threshold_clock')
    return {'allowed':not reasons,'reasons':list(dict.fromkeys(reasons))}


def evidence_link(hypothesis,experiment):
    """A link states what was tested, not that a profitable strategy was found."""
    identity=hypothesis.get('hypothesis_id')
    tested=experiment.get('tested_hypothesis_ids',[])
    reasons=[]
    if not identity or identity not in tested:reasons.append('hypothesis_not_explicitly_tested')
    if experiment.get('hypothesis_versions',{}).get(identity)!=hypothesis.get('rule_version') or not hypothesis.get('rule_version'):
        reasons.append('hypothesis_version_not_matched')
    if experiment.get('status')!='completed' or not experiment.get('manifest_sha256'):
        reasons.append('completed_result_evidence_missing')
    return {'evidence_linked':not reasons,'profitability_proven':False,'reasons':reasons}
