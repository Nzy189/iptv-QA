#!/usr/bin/env python3
"""ECS-owned workflow: synchronize catalog, validate Chinese, then English."""
import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import sys

from iptv_qa.detection import checker as app
from iptv_qa.catalog.sources import synchronize, classify, playlist


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='/etc/iptv-qa/config.json')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    cfg = json.loads(Path(args.config).read_text(encoding='utf-8'))
    if cfg.get('channel_library', {}).get('enabled'):
        from iptv_qa.library.store import configured_path
        cfg['channel_library'] = dict(cfg['channel_library'],path=str(configured_path(cfg)),data_root=cfg['data_dir'])
    data = Path(cfg['data_dir'])
    data.mkdir(parents=True, exist_ok=True)
    import fcntl
    lock = (data / 'pipeline.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logging.info('Another catalog pipeline is already active')
        return 0
    state = {'state': 'running', 'started_at': app.utcnow()}
    def progress(phase):
        state.update(phase=phase, updated_at=app.utcnow())
        app.write_json(data / 'pipeline.json', state)
        logging.info('Pipeline phase: %s', phase)
    try:
        root, database, inventory = synchronize(cfg, app.parse_playlist, app.normalize, app.write_json, app.atomic_text, progress)
        state['catalog_counts'] = inventory['counts']
        progress('chinese')
        chinese_result = app.main(['--config', args.config])
        state['chinese_exit_code'] = chinese_result
        english = app.parse_playlist((root / 'en.m3u').read_text(encoding='utf-8')) if inventory['counts'].get('en_unique_addresses') else []
        # Include English channels found in the original two sources as well.
        status = json.loads((data / 'status.json').read_text(encoding='utf-8'))
        run = data / 'runs' / status['run_id']
        for source in cfg['sources']:
            if source.get('catalog'):
                continue
            original = run / (source['name'] + '.m3u')
            if original.exists():
                try:
                    english.extend(e for e in app.parse_playlist(original.read_text(encoding='utf-8')) if classify(e, database) == 'en')
                except ValueError:
                    pass
        if english:
            app.atomic_text(root / 'english-all.m3u', playlist(english))
            english_cfg = dict(cfg)
            english_cfg['data_dir'] = str(data / 'english')
            english_cfg['language_scope'] = 'en'
            english_cfg['sources'] = [{'name': 'english', 'url': (root / 'english-all.m3u').as_uri()}]
            english_cfg['summaries'] = {'enabled': False}
            english_cfg['obs'] = dict(cfg.get('obs', {}), publish_sets=False, upload_report=False,
                                      playlist_files=['iptv_top3.m3u'], object_names={'iptv_top3.m3u': 'iptv_english.m3u'})
            path = root / 'english-config.json'
            app.write_json(path, english_cfg)
            progress('english')
            state['english_exit_code'] = app.main(['--config', str(path)])
        else:
            state['english_exit_code'] = 0
            state['english_note'] = 'No English candidates identified'
        state.update(state='complete' if chinese_result == 0 and state['english_exit_code'] == 0 else 'failed', finished_at=app.utcnow())
        progress('finished')
        app.update_run_summaries(cfg)
        return 0 if state['state'] == 'complete' else 2
    except Exception as exc:
        state.update(state='failed', error_type=type(exc).__name__, finished_at=app.utcnow())
        progress('failed')
        logging.error('Catalog pipeline failed (%s)', type(exc).__name__)
        return 1
    finally:
        lock.close()


if __name__ == '__main__':
    sys.exit(main())
