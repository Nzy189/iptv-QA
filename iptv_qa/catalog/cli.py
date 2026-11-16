"""Candidate discovery without FFmpeg or player dependencies."""
import argparse
import json
import logging
from pathlib import Path
from iptv_qa.common.io import atomic_text, write_json
from iptv_qa.common.playlists import normalize, parse_playlist
from iptv_qa.common.locking import exclusive_lock
from .sources import synchronize


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/etc/iptv-qa/config.json')
    args = parser.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text(encoding='utf-8'))
    try:
        with exclusive_lock(Path(cfg['data_dir']) / 'pipeline.lock'):
            root, _, inventory = synchronize(cfg, parse_playlist, normalize, write_json, atomic_text)
    except BlockingIOError:
        logging.error('Another catalogue workflow is active')
        return 2
    print('Catalogue ready: {} files; Chinese {} endpoints; English {} endpoints'.format(
        inventory['counts'].get('downloaded_files', 0), inventory['counts'].get('zh_unique_addresses', 0),
        inventory['counts'].get('en_unique_addresses', 0)))
    return 0
