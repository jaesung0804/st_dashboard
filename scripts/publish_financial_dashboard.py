"""Publish saved investment metrics; preserve every other Pages path."""
import os
from pathlib import Path
import tempfile

from build_pages_deploy import push_pages
from export_investment_experiment import export


def main():
    if os.getenv('RESEARCH_STORAGE') != 'backend':
        raise ValueError('Publishing financial metrics requires the configured backend')
    with tempfile.TemporaryDirectory(prefix='financial-publication-') as temporary:
        root = Path(temporary)
        site = root / 'site'
        if not export(site, cache=root / 'cache'):
            raise ValueError('Missing reviewed investment presentation')
        push_pages(site, 'https://github.com/jaesung0804/st_dashboard.git')


if __name__ == '__main__':
    main()
