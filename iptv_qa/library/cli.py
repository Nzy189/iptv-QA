"""Library migration, version publication, encrypted backup and job worker."""
import argparse
import datetime
import json
import logging
from pathlib import Path
import time

from .store import Library, configured_path
from .publishing import Publisher


def migrate(library, cfg):
    data = Path(cfg['data_dir'])
    candidates = {'main': [data/'published/current/iptv_top5.m3u', data/'published/current/iptv_top3.m3u'],
                  'supplement':[data/'extras/iptv_set4.m3u'],
                  'english':[data/'english/published/current/iptv_top3.m3u']}
    result = {}
    for scope, files in candidates.items():
        text = '\n'.join(p.read_text(encoding='utf-8-sig') for p in files if p.is_file())
        if text.strip() and '#EXTINF:' in text:
            report_path = files[0].parent/'report.json'
            report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
            result[scope] = library.ingest(scope,text,'migration',report=report)
    return result


def reconcile_external(library,cfg):
    data=Path(cfg['channel_library'].get('data_root') or cfg['data_dir'])
    changed=False
    for scope,folder in [('main',data/'published/current'),('english',data/'english/published/current')]:
        report_path=folder/'report.json'
        if not report_path.is_file():continue
        report=json.loads(report_path.read_text(encoding='utf-8'))
        if report.get('library_version') or not report.get('published') or report.get('diagnostic_sample'):continue
        with library.connect() as db:prior=library.get_meta(db,'report:'+scope,{})
        if report.get('run_id')==prior.get('run_id'):continue
        files=[folder/'iptv_top5.m3u',folder/'iptv_top3.m3u']
        content='\n'.join(p.read_text(encoding='utf-8-sig') for p in files if p.is_file())
        if '#EXTINF:' not in content:continue
        library.ingest(scope,content,'external-qa:'+report.get('run_id','unknown'),report=report)
        changed=True
    if changed:library.enqueue('publish',library.revision())


def process_jobs(library, cfg):
    from .backup import backup_library
    with library.connect() as db:
        jobs = [dict(r) for r in db.execute("SELECT * FROM jobs WHERE state IN ('pending','failed') ORDER BY rowid")]
    for job in jobs:
        if job['state']=='failed':
            import datetime
            updated=datetime.datetime.strptime(job['updated_at'],'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc).timestamp()
            if time.time()-updated<60:continue
        state, error = 'done', ''
        try:
            if job['kind'] == 'publish':
                if job['revision'] != library.revision():
                    state = 'superseded'
                else:
                    result = Publisher(cfg, library=library).publish(job['revision'])
                    if result['state'] == 'sync_failed':
                        state, error = 'failed', result['error']
            else:
                result = backup_library(library, cfg)
                if result['state'] != 'verified':
                    state, error = 'failed', result['error']
        except Exception as exc:
            state, error = 'failed', type(exc).__name__
        from iptv_qa.common.io import utcnow
        with library.connect() as db:
            db.execute('UPDATE jobs SET state=?,error=?,updated_at=? WHERE id=?', (state,error,utcnow(),job['id']))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('migrate','status','publish','backup','worker','restore'))
    parser.add_argument('--config', default='/etc/iptv-qa/config.json')
    parser.add_argument('--snapshot')
    parser.add_argument('--destination')
    args = parser.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text(encoding='utf-8'))
    path = configured_path(cfg)
    if path is None:
        raise ValueError('channel_library_not_enabled')
    library = Library(path)
    if args.operation == 'migrate':
        print(json.dumps(migrate(library,cfg)))
    elif args.operation == 'publish':
        result = Publisher(cfg,library=library).publish()
        print(json.dumps(result))
        return int(result['state'] == 'sync_failed')
    elif args.operation == 'backup':
        from .backup import backup_library
        result = backup_library(library,cfg)
        print(json.dumps(result))
        return int(result['state'] != 'verified')
    elif args.operation == 'restore':
        from .backup import restore_backup
        if not args.snapshot or not args.destination:
            parser.error('restore requires --snapshot and --destination (a new path)')
        print(json.dumps(restore_backup(args.snapshot, cfg['channel_library']['backup']['key_path'], args.destination)))
    elif args.operation == 'status':
        print(json.dumps(library.admin_state('main')))
    else:
        # A persistent service retries pending jobs; it is not a second QA scheduler.
        from iptv_qa.common.locking import exclusive_lock
        with exclusive_lock(library.path.parent/'worker.lock'):
            while True:
                try:
                    reconcile_external(library,cfg)
                except (OSError,ValueError):
                    pass
                try:
                    process_jobs(library,cfg)
                except Exception as error:
                    logging.error('Library job processing: %s',type(error).__name__)
                backup = cfg['channel_library'].get('backup',{})
                if backup.get('enabled'):
                    with library.connect() as db:
                        due = library.get_meta(db,'next_backup',0)
                        if time.time() >= due:
                            library._enqueue(db,'backup',library.get_meta(db,'revision',0))
                            library.put_meta(db,'next_backup',int(time.time())+86400)
                time.sleep(10)
    return 0
