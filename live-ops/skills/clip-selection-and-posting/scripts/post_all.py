"""Compatibility CLI for the verified, staggered posting pipeline."""
import argparse
import os
from pathlib import Path
import subprocess

PLATFORMS = ('yt', 'tt', 'ig', 'x', 'sc')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('vod')
    p.add_argument('clip')
    p.add_argument('platforms', nargs='?', default=','.join(PLATFORMS))
    p.add_argument('--stagger', action='store_true', help='accepted for compatibility; posting is always staggered')
    a = p.parse_args()
    want = set(a.platforms.lower().split(','))
    if not want or not want <= set(PLATFORMS):
        p.error('platforms must be a comma-separated list of yt,tt,ig,x,sc')
    script = Path(__file__).resolve().parents[3] / 'posting' / 'post_all.sh'
    skip = '|'.join(x.upper() for x in PLATFORMS if x not in want)
    return subprocess.call(['bash', str(script), a.vod, a.clip],
                           env={**os.environ, 'SKIP': skip or os.getenv('SKIP', '')})


if __name__ == '__main__':
    raise SystemExit(main())