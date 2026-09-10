from ai_stock_assistant.investment_research import research_usage_gate,evidence_link


def main():
    hypothesis={'hypothesis_id':'NOW-quarter-v1','known_at':'2026-09-10T09:20:00Z','executable':False}
    assert 'hypothesis_not_known_at_decision' in research_usage_gate(hypothesis,'2026-07-13T20:00:00Z')['reasons']
    valid=hypothesis|{'executable':True,'rule_version':'v1','thresholds_registered_at':'2026-09-10T09:20:00Z',
                     'decision_inputs':[{'available_at':'2026-09-10T10:00:00Z'}]}
    assert research_usage_gate(valid,'2026-09-11T20:00:00Z')['allowed']
    assert not research_usage_gate(valid|{'decision_inputs':[{'available_at':'2026-09-12T10:00:00Z'}]},'2026-09-11T20:00:00Z')['allowed']
    price_test={'ticker':'NOW','horizon':'quarter','status':'completed','manifest_sha256':'a'*64}
    assert not evidence_link(valid,price_test)['evidence_linked']
    exact=price_test|{'tested_hypothesis_ids':['NOW-quarter-v1'],'hypothesis_versions':{'NOW-quarter-v1':'v1'}}
    assert evidence_link(valid,exact)['evidence_linked']
    assert not evidence_link(valid,exact)['profitability_proven']
    assert not evidence_link(valid,exact|{'hypothesis_versions':{'NOW-quarter-v1':'v2'}})['evidence_linked']
    print('PASS current-thesis clock, future inputs, exact hypothesis/version linkage and no automatic profitability claim')


if __name__=='__main__':main()
