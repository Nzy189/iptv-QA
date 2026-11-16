"""Generate reports without a detector configuration or media binaries."""
import argparse
import logging
from pathlib import Path
from iptv_qa.common.locking import exclusive_lock
from .history import refresh


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--public-base', default='')
    args = parser.parse_args(argv)
    try:
        with exclusive_lock(Path(args.data_dir) / 'run.lock'):
            target, count = refresh(args.data_dir, args.public_base)
    except BlockingIOError:
        logging.error('Another check or maintenance operation is active')
        return 2
    print('Reports generated: {} run summaries in {}'.format(count, target))
    return 0
