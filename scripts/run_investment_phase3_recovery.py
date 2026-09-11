"""Nine read-only, source-verified r04 recovery comparisons; no round closure.

Restore only retained US prices and filing evidence. Publish no DB changes.
The compact recovery archive contains simulated trades and NAV, never sources,
credentials, models, or agent histories. It must be saved before claiming a
durable result. Historical SPY curves are reused only after exact date checks.
"""
from __future__ import annotations

import base64
from collections import defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import time
import zipfile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ai_stock_assistant.investment_replay import metrics, prepare, sha256
from ai_stock_assistant.investment_replay_inputs import load_retained_prices
from ai_stock_assistant.investment_replay_v2 import replay_v2
from ai_stock_assistant.investment_rounds import validate_roster_lock
from investment_phase3_preflight import INPUTS, ReadOnlyClient
from research_backend_client import BackendError
from run_employee_evolution import GENOMES
from unpack_dashboard_state import restore_split

MODES = ('efficient_control', 'two_team_support', 'all_team_support')
CONDITIONS = (('base', 1, 1, 'efficient_control-p0-c1'),
              ('double_cost', 2, 1, 'efficient_control-p0-c2'),
              ('extra_delay', 1, 2, 'efficient_control-p0-c1-delay2'))
SUPPORT_TEAMS = {'compound_quarter', 'adaptive_defensive'}
HISTORICAL = ROOT / 'docs/replays/2026-09-10-r03-fixed-roster'
MAX_DOWNLOAD = 190_000_000
MAX_ARCHIVE = 1_900_000


def packed(content):
    return gzip.compress(content, compresslevel=9, mtime=0)


def body(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')) + '\n').encode()


def assignments(people, mode, first_date):
    if mode not in MODES:
        raise ValueError('Undeclared allocation')
    by_team = defaultdict(list)
    for person in people:
        by_team[person['team']].append(person)
    if len(people) != 35 or len(by_team) != 9:
        raise ValueError('Unexpected frozen roster')
    schedule = {first_date: {}}
    for team, staff in by_team.items():
        efficient = next(p for p in staff if p['id'] == team + '-E02' and p['genome'] == 'efficient')
        guarded = next(p for p in staff if p['id'] == team + '-E03' and p['genome'] == 'guarded')
        support = mode == 'all_team_support' or mode == 'two_team_support' and team in SUPPORT_TEAMS
        selected = [(efficient, .7), (guarded, .3)] if support else [(efficient, 1.)]
        schedule[first_date][team] = [dict(id=p['id'], weight=w, **GENOMES[p['genome']]) for p, w in selected]
    return schedule


def restore_inputs(client, work):
    head = client.json('GET', '/snapshot-heads/pipeline-state')
    sid = head['snapshot_id']
    if not sid:
        raise ValueError('Missing migrated snapshot')
    entries = {e['relative_path']: e for e in client.snapshot_entries(sid)}
    entry = entries['state-manifest.json']
    if entry['byte_size'] > 500_000:
        raise ValueError('Manifest exceeds recovery budget')
    manifest_path = work / 'state-manifest.json'
    client.download(entry['sha256'], manifest_path, entry['byte_size'])
    manifest = json.loads(manifest_path.read_text())
    wanted = ('data/raw/us_ohlcv_nasdaq_nyse_yfinfo_state.csv', 'data/dashboard_research')
    if manifest[wanted[0]]['sha256'] != INPUTS['raw_us_prices']:
        raise ValueError('Migrated price source differs from the declared experiment')
    parts = [part for path in wanted for part in manifest[path]['parts']]
    if sum(entries[p]['byte_size'] for p in parts) > MAX_DOWNLOAD:
        raise ValueError('Bounded download budget exceeded')
    for part in parts:
        entry = entries[part]
        client.download(entry['sha256'], work / part, entry['byte_size'])
    raw = work / 'us-prices.csv'
    restore_split(work, raw, manifest[wanted[0]])
    archive = work / 'research.tar'
    restore_split(work, archive, manifest[wanted[1]])
    filings = work / 'filing_events.csv.gz'
    name = 'data/dashboard_research/accounting/us/filing_events.csv.gz'
    with tarfile.open(archive, 'r') as source:
        member = source.getmember(name)
        if not member.isfile() or member.size > 30_000_000:
            raise ValueError('Unexpected filing archive member')
        with source.extractfile(member) as stream:
            content = stream.read(member.size + 1)
        if len(content) != member.size or hashlib.sha256(content).hexdigest() != INPUTS['filing_events']:
            raise ValueError('Filing evidence differs from the declared experiment')
        filings.write_bytes(content)
    return raw, filings, sid


def main():
    started = time.perf_counter()
    client = ReadOnlyClient(project='investment')
    # Read selected current documents; do not fall back to mutable Git copies.
    client.json('GET', '/records/rounds?limit=20')
    state = client.read_json('data/reference/investment_rounds.json')
    if any(x.get('id') == 'r04' or x.get('status') == 'active' for x in state['rounds']):
        raise ValueError('Existing or active round requires checkpoint-aware resume')
    previous = next(x for x in state['rounds'] if x['id'] == 'r03' and x['status'] == 'completed')
    organization = client.read_json('data/reference/investment_organization.json')
    if not validate_roster_lock(previous, organization)['valid']:
        raise ValueError('Roster changed since the declared comparison')
    proposal = client.read_json('data/reference/investment_next_round_proposal.json')
    if [x['id'] for x in proposal['candidate_allocations']] != list(MODES):
        raise ValueError('Allocation proposal changed')
    expected_conditions = [('base', 10, 1), ('double_cost', 20, 1), ('extra_delay', 10, 2)]
    if [(x['id'], x['one_way_cost_bps'], x['fill_delay_sessions']) for x in proposal['fixed_execution_cases']] != expected_conditions:
        raise ValueError('Execution proposal changed')
    people = previous['strategy_employees']
    policy = json.loads((ROOT / 'data/reference/employee_supervision_policy.json').read_text())
    old_protocol = json.loads((HISTORICAL / 'protocol.json').read_text())
    if policy != old_protocol['policy']:
        raise ValueError('Execution policy differs from retained controls')
    manifest = json.loads((HISTORICAL / 'manifest.json').read_text())
    cohort = ROOT / 'data/reference/accounting_cohort.json'
    if sha256(cohort) != INPUTS['cohort']:
        raise ValueError('Cohort changed')
    registered = datetime.now(timezone.utc).isoformat()
    protocol = dict(schema='investment-r04-recovery-v1', registered_at=registered,
        source_proposal_registered_at=proposal['registered_at'], internal_round='r04', user_round=3,
        status='recovery_comparison_not_formal_round', personnel_count=35, new_role_registration='pending',
        actual_hour_round_completed=False, independently_approved=False,
        cases=[dict(id=f'{mode}-{condition}', mode=mode, cost_multiplier=cost, fill_delay=delay, phase=0)
            for condition, cost, delay, _ in CONDITIONS for mode in MODES],
        input_hashes=INPUTS, policy_sha256=sha256(ROOT / 'data/reference/employee_supervision_policy.json'),
        roster_sha256=hashlib.sha256(body(people)).hexdigest(),
        caveat='Previously examined sample; exploratory rule replay, not independent OOS or historical AI active performance.',
        benchmark='Retained SPY90/cash10 curves with exact matching source dates, cost and delay; no team NAV splicing.')
    output = ROOT / '.research-backend/artifacts/r04-recovery'
    output.mkdir(parents=True, exist_ok=False)
    payloads = {'protocol.json': body(protocol)}
    runs = {}
    with tempfile.TemporaryDirectory(prefix='investment-r04-input-') as folder:
        work = Path(folder)
        raw, filing, snapshot = restore_inputs(client, work)
        prices, provenance = load_retained_prices(raw, cohort, work / 'local-cache.pkl', 'us')
        filings = pd.read_csv(filing, dtype={'ticker': str, 'filing_id': str})
        data = prepare(prices, filings, policy, 'us')
        dates = [str(d.date()) for d in data.dates[data.dates.searchsorted(policy['start']):]]
        for condition, cost, delay, historical_case in CONDITIONS:
            nav_file = HISTORICAL / historical_case / 'nav.csv.gz'
            summary_file = HISTORICAL / historical_case / 'summary.json'
            for file in (nav_file, summary_file):
                if sha256(file) != manifest['artifacts'][file.relative_to(HISTORICAL).as_posix()]:
                    raise ValueError('Historical comparison evidence changed')
            old_nav = pd.read_csv(nav_file)
            if old_nav['date'].tolist() != dates:
                raise ValueError('Benchmark dates do not match replay')
            benchmark = old_nav['spy_cash_matched'].to_numpy()
            old_summary = json.loads(summary_file.read_text())
            for mode in MODES:
                case_id = f'{mode}-{condition}'
                schedule = assignments(people, mode, dates[0])
                result = replay_v2(data, policy | {'employee_schedule': schedule}, 'efficient',
                    cost_multiplier=cost, fill_delay=delay, rebalance_phase=0, record_details=True)
                # A source rebuild must reproduce every retained control curve.
                if mode == 'efficient_control':
                    for company in policy['companies']:
                        np.testing.assert_allclose(result['series'][company], old_nav[company].to_numpy(), rtol=1e-10, atol=1e-6)
                for trade in result['trades']:
                    if trade['fill_date'] <= trade['signal_date']:
                        raise ValueError('Same-day or backward fill')
                for company, cfg in policy['companies'].items():
                    costs = sum(t['cost'] for t in result['trades'] if t['desk'] in cfg['teams'])
                    if not np.isclose(costs, result['summary'][company]['cost_paid'], rtol=1e-10, atol=1e-6):
                        raise ValueError('Trade cost ledger does not reconcile')
                summary = dict(id=case_id, mode=mode, condition=condition, summary=result['summary'],
                    benchmark=old_summary['summary']['spy_cash_matched'],
                    benchmark_nav_sha256=sha256(nav_file), unfilled_order_batches=result['unfilled_order_batches'],
                    trade_rows=len(result['trades']), decision_rows=len(result['decisions']),
                    decision_fingerprint=hashlib.sha256(body(result['decisions'])).hexdigest(),
                    full_decision_payload_retained=False,
                    checks=['signal_before_fill', 'trade_cost_reconciles', 'shared_company_volume_capacity_engine'])
                runs[case_id] = summary
                payloads[f'{case_id}/summary.json'] = body(summary)
                payloads[f'{case_id}/trades.csv.gz'] = packed(pd.DataFrame(result['trades']).to_csv(index=False).encode())
                payloads[f'{case_id}/company-nav.csv.gz'] = packed(pd.DataFrame({'date': dates,
                    **{c: result['series'][c] for c in policy['companies']}, 'spy_cash_matched': benchmark}).to_csv(index=False).encode())
                print('PHASE3_CASE=' + json.dumps({'id': case_id, 'trade_rows': len(result['trades']), 'status': 'computed_unarchived'}), flush=True)
        summary = dict(schema=protocol['schema'], registered_at=registered, completed_at=datetime.now(timezone.utc).isoformat(),
            status='computed_pending_archive_and_independent_review', case_count=len(runs), period=[dates[0], dates[-1]],
            source_snapshot_id=snapshot, source_revalidated=True, price_provenance=provenance,
            historical_controls_reproduced=3, runs=runs, paid_data_calls=0, llm_api_calls=0,
            db_writes=0, deletions=0, actual_hour_round_completed=False, new_role_registration='pending',
            elapsed_seconds=round(time.perf_counter()-started, 3))
    payloads['summary.json'] = body(summary)
    file_manifest = {path: dict(bytes=len(content), sha256=hashlib.sha256(content).hexdigest()) for path, content in payloads.items()}
    payloads['manifest.json'] = body(dict(files=file_manifest, retention='Save immutable recovery archive before referencing results; backend sync pending.'))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, content in payloads.items():
            archive.writestr(path, content)
    raw_archive = buffer.getvalue()
    if len(raw_archive) > MAX_ARCHIVE or any(len(c) > 500_000 for c in payloads.values()):
        raise ValueError('Recovery evidence exceeds declared small-file budget')
    (output / 'recovery.zip').write_bytes(raw_archive)
    print('PHASE3_RESULT=' + body(summary).decode().strip(), flush=True)
    encoded = base64.b64encode(raw_archive).decode()
    for index in range(0, len(encoded), 48_000):
        print(f'PHASE3_ARCHIVE_{index//48_000:04d}=' + encoded[index:index+48_000], flush=True)
    print('PHASE3_ARCHIVE_META=' + json.dumps({'bytes': len(raw_archive), 'sha256': hashlib.sha256(raw_archive).hexdigest(), 'chunks': (len(encoded)+47_999)//48_000}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        safe = {'status': 'failed_preserved', 'error_type': type(error).__name__, 'db_writes': 0}
        # Only locally defined constant error text is safe for public output.
        if isinstance(error, BackendError):
            safe['http_status'] = error.status
        elif isinstance(error, (ValueError, AssertionError, KeyError)):
            safe['reason'] = str(error)[:300]
        print('PHASE3_FAILURE=' + json.dumps(safe), flush=True)
        sys.exit(1)
