"""Check research leaves original price/model/forecast bytes untouched."""
import argparse, hashlib, json
from pathlib import Path


def snapshot():
    paths = list(Path('data/dashboard_ews').rglob('*'))
    paths += list(Path('data/raw').glob('*_state.csv'))
    return {p.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths) if p.is_file()}


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('stage', choices=['before', 'after'])
    args = parser.parse_args(); checkpoint = Path('.work/production-hashes.json')
    if args.stage == 'before':
        checkpoint.parent.mkdir(exist_ok=True)
        checkpoint.write_text(json.dumps(snapshot()))
        return
    expected = json.loads(checkpoint.read_text())
    assert snapshot() == expected, 'Research changed production source bytes'
    for market in ['kr', 'us']:
        root = Path('outputs') / f'lgbm_warning_dashboard_macro_{market}_latest'
        manifest = json.loads((root / 'manifest.json').read_text())
        recon = Path('data/dashboard_research/reconstruction') / market / 'predictions'
        required = {p.parent.name for p in recon.glob('*/rows.json')}
        assert required.issubset(set(manifest['dates'])), 'Missing reconstructed date'
        for date in required:
            file = root / 'walkforward_scores_by_date' / (date + '.json')
            originals = [Path('data/dashboard_ews') / market / 'predictions' / date / 'rows.json',
                         Path('data/dashboard_ews') / market / 'legacy' / (date + '.json')]
            original = next((p for p in originals if p.exists()), None)
            if original:
                assert file.read_bytes() == original.read_bytes(), 'Replaced a stored original'
            else:
                assert manifest['predictionKindsByDate'][date] == 'reconstructed'
                assert all(r['predictionKind'] == 'reconstructed' for r in json.loads(file.read_text()))
        print(market, len(required), 'requested sessions present; all originals unchanged')


if __name__ == '__main__':
    main()
