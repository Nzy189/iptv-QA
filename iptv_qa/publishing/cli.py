"""Publish an existing export without requiring source URLs or media tools."""
import argparse
import json
import logging
from pathlib import Path
from iptv_qa.common.io import write_json
from iptv_qa.common.locking import exclusive_lock
from .obs import publish_obs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/etc/iptv-qa/config.json')
    parser.add_argument('--scope', choices=('main', 'english'), default='main')
    args = parser.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text(encoding='utf-8'))
    from iptv_qa.library.store import configured_path
    if configured_path(cfg) is not None and args.scope == 'main':
        from iptv_qa.library.publishing import Publisher
        result = Publisher(cfg).publish()
        print('Library publication ' + result['state'])
        return 2 if result['state']=='sync_failed' else 0
    data = Path(cfg['data_dir'])
    obs = dict(cfg.get('obs', {}))
    if args.scope == 'english':
        data = data / 'english'
        obs.update(playlist_files=['iptv_top3.m3u'], publish_sets=False, upload_report=False,
                   object_names={'iptv_top3.m3u':'iptv_english.m3u'})
    # Same lock file as QA/maintenance; no detector import or media self-check.
    with exclusive_lock(data / 'run.lock'):
        export = (data / 'published/current').resolve()
        report_path = export / 'report.json'
        report = json.loads(report_path.read_text(encoding='utf-8'))
        if not report.get('published') or report.get('diagnostic_sample'):
            raise RuntimeError('No complete validated publication is available')
        try:
            report['obs'] = publish_obs(export, obs)
            write_json(report_path, report)
        except Exception as error:
            report['obs'] = {'enabled': bool(obs.get('enabled')), 'state':'failed', 'error':type(error).__name__}
            write_json(report_path, report)
            raise
    print('Publication {}'.format(report['obs']['state']))
    return 0
