"""Immutable library versions; local switches and retriable OBS delivery."""
import hashlib
import json
import os
from pathlib import Path
import uuid

from iptv_qa.common.io import atomic_text, utcnow, write_json
from iptv_qa.common.locking import exclusive_lock
from iptv_qa.publishing.obs import publish_obs
from .store import Library, configured_path


def render(rows, limit=5, single=None, stable=False):
    lines = ['#EXTM3U']
    for row in rows:
        candidates = [r for r in row['lines'] if not stable or r['evidence'] == 'stable'][:limit]
        if single is not None:
            candidates = [candidates[min(single, len(candidates)-1)]] if candidates else []
        for route in candidates:
            attrs = json.loads(row['metadata'])
            attrs['group-title'] = row['group_name']
            attrs['channel-id'] = row['id']
            prefix = '#EXTINF:-1' + ''.join(' %s="%s"' % (k, str(v).replace('"', '').replace('\n', '').replace('\r', ''))
                for k,v in attrs.items() if k in ('tvg-id', 'tvg-name', 'tvg-logo', 'group-title', 'channel-id'))
            lines.append(prefix + ',' + row['name'])
            if route['headers'].get('user_agent'):
                lines.append('#EXTVLCOPT:http-user-agent=' + route['headers']['user_agent'])
            if route['headers'].get('referer'):
                lines.append('#EXTVLCOPT:http-referrer=' + route['headers']['referer'])
            lines.append(route['url'])
    return '\n'.join(lines) + '\n'


def switch_local(data, export):
    data.mkdir(parents=True, exist_ok=True)
    current = data / 'current'
    temporary = data / ('.library-' + uuid.uuid4().hex)
    temporary.symlink_to(export.resolve(), target_is_directory=True)
    os.replace(str(temporary), str(current))
    for path in export.glob('*'):
        if path.suffix not in ('.m3u', '.json'):
            continue
        alias = data / path.name
        if alias.is_symlink():
            continue
        if alias.exists():
            raise RuntimeError('Unmanaged publication file')
        alias.symlink_to('current/' + path.name)
    if (export / 'iptv_top5.m3u').exists():
        alias = data / 'iptv_ok.m3u'
        if not alias.exists() and not alias.is_symlink():
            alias.symlink_to('current/iptv_top5.m3u')


class Publisher:
    def __init__(self, cfg, library=None, upload=None):
        self.cfg = cfg
        self.library = library or Library(configured_path(cfg))
        self.root = self.library.path.parent
        self.data = Path(cfg['channel_library'].get('data_root') or cfg['data_dir'])
        self.upload = upload or publish_obs

    def publish(self, expected=None, retry=False):
        with exclusive_lock(self.root / 'publish.lock'):
            snapshot = self.library.snapshot()
            if expected is not None and snapshot['revision'] != expected:
                raise ValueError('publication_revision_changed')
            rows = {scope: Library.view_snapshot(snapshot, scope) for scope in ('main', 'live', 'english')}
            with self.library.connect() as db:
                prior = db.execute('SELECT * FROM versions ORDER BY rowid DESC LIMIT 1').fetchone()
            reuse = bool(prior and prior['revision'] == snapshot['revision'] and prior['state'] in ('pending', 'sync_failed'))
            identity = prior['id'] if reuse else uuid.uuid4().hex
            version = self.root / 'versions' / identity
            manifest_path = version / 'manifest.json'
            if reuse:
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            else:
                version.mkdir(parents=True)
                if os.name != 'nt':
                    version.chmod(0o700)
                write_json(version / 'state.json', snapshot)
                contents = {'main/iptv_top5.m3u': render(rows['main']),
                    'main/iptv_top3.m3u': render(rows['main'], 3),
                    'main/iptv_stable.m3u': render(rows['main'], stable=True),
                    'live/iptv_live.m3u': render(rows['live']),
                    'english/iptv_top3.m3u': render(rows['english'], 3)}
                for index in range(3):
                    contents['main/iptv_set%d.m3u' % (index+1)] = render(rows['main'], single=index)
                manifest = dict(id=identity, revision=snapshot['revision'], created_at=utcnow(), files={})
                for name, content in contents.items():
                    target = version / name
                    atomic_text(target, content)
                    manifest['files'][name] = dict(sha256=hashlib.sha256(content.encode()).hexdigest(),
                        entries=content.count('#EXTINF:'), state='pending')
                for scope in ('main', 'english'):
                    report = dict(snapshot['reports'][scope], published=True, diagnostic_sample=False,
                        library_version=identity, library_revision=snapshot['revision'],
                        channels_available=len(rows[scope]),
                        top5_entries=sum(min(5,len(r['lines'])) for r in rows[scope]),
                        top3_entries=sum(min(3,len(r['lines'])) for r in rows[scope]),
                        obs={'state':'pending'})
                    write_json(version / scope / 'report.json', report)
                write_json(manifest_path, manifest)
                with self.library.connect() as db:
                    db.execute('INSERT INTO versions VALUES (?,?,?,?,?,?)',
                        (identity, snapshot['revision'], 'pending', json.dumps(manifest), '', utcnow()))
                    controls = dict(revision=snapshot['revision'],channels=[{k:r[k] for k in ('id','name','group_name','state')} for r in snapshot['channels']],memberships=snapshot['memberships'],routes=[{k:r[k] for k in ('channel_id','scope','stream_id','priority','enabled')} for r in snapshot['routes']])
                    db.execute('INSERT INTO version_states VALUES (?,?)',(identity,json.dumps(controls)))
            switch_local(self.data / 'published', version / 'main')
            switch_local(self.data / 'english/published', version / 'english')
            atomic_text(self.data / 'extras/iptv_live.m3u', (version / 'live/iptv_live.m3u').read_text(encoding='utf-8'))
            names = self.cfg.get('obs', {}).get('object_names', {})
            try:
                for relative, info in manifest['files'].items():
                    if info['state'] in ('uploaded', 'not_configured'):
                        continue
                    path = version / relative
                    obj = ('iptv_english.m3u' if relative.startswith('english/') else
                           names.get(path.name, path.name))
                    obs = dict(self.cfg.get('obs', {}), object_names={path.name: obj})
                    # Write immutable version objects before fixed subscription keys.
                    archive = dict(obs, prefix=obs.get('prefix','').strip('/') + '/versions/' + identity + '/' + relative.split('/')[0])
                    self.upload(path.parent, archive, filenames=(path.name,))
                    result = self.upload(path.parent, obs, filenames=(path.name,))
                    info['state'] = result['state']
                    write_json(manifest_path, manifest)
                # A final manifest is a completion marker, not an atomic multi-key upload.
                public_manifest = version / 'public'
                public_manifest.mkdir(exist_ok=True)
                write_json(public_manifest / 'library_version.json', manifest)
                self.upload(public_manifest, self.cfg.get('obs', {}), filenames=('library_version.json',))
                state, error = 'synced' if self.cfg.get('obs', {}).get('enabled') else 'local_only', ''
            except Exception as exc:
                state, error = 'sync_failed', type(exc).__name__
            with self.library.connect() as db:
                db.execute('UPDATE versions SET state=?,manifest=?,error=? WHERE id=?', (state,json.dumps(manifest),error,identity))
                if state != 'sync_failed':
                    self.library.put_meta(db, 'published_revision', snapshot['revision'])
            for scope in ('main', 'english'):
                path = version / scope / 'report.json'
                report = json.loads(path.read_text(encoding='utf-8'))
                report['obs'] = {'state': 'uploaded' if state == 'synced' else 'not_configured' if state == 'local_only' else 'failed'}
                write_json(path, report)
            return dict(id=identity, revision=snapshot['revision'], state=state, error=error)
