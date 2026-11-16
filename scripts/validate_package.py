#!/usr/bin/env python3
"""Verify a built wheel from an isolated directory, including player assets."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='iptv-package-') as temp:
        target = Path(temp).resolve()
        with zipfile.ZipFile(args.wheel) as archive:
            names = set(archive.namelist())
            for asset in ('index.html','app.js','style.css','hls.min.js','LICENSE-hlsjs.txt'):
                assert 'iptv_qa/playback/assets/'+asset in names, 'Missing packaged player asset: '+asset
            for name in archive.namelist():
                if target not in (target/name).resolve().parents:
                    raise ValueError('Unsafe wheel path')
            archive.extractall(target)
        code = '''import sys
sys.path.insert(0,sys.argv[1])
from iptv_qa.playback.service import assets_dir, TVPlugin
from iptv_qa.server.http import make_handler
assert (assets_dir()/'index.html').is_file()
assert (assets_dir()/'hls.min.js').stat().st_size > 1000
assert 'iptv_qa.detection.checker' not in sys.modules
import tv, web, iptv_checker
from iptv_qa.cli import main
main(['play','--help'])
'''
        # --help exits with 0, after the asset and dependency assertions.
        subprocess.check_call([sys.executable,'-I','-c',code,str(target)],cwd=str(target))
    print('INSTALLED_WHEEL_AND_PLAYER_ASSETS_OK')


if __name__ == '__main__':
    main()
