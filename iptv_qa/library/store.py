"""SQLite source of truth. Connections are short-lived and safe across threads."""
import collections
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import uuid
from urllib.parse import urlsplit

from iptv_qa.common.io import utcnow
from iptv_qa.common.playlists import ATTR, endpoint, parse_playlist


SCHEMA = '''
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS channels (id TEXT PRIMARY KEY, name TEXT NOT NULL,
 group_name TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL, updated_at TEXT NOT NULL, metadata TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS aliases (scope TEXT, alias TEXT, channel_id TEXT NOT NULL,
 PRIMARY KEY(scope,alias), FOREIGN KEY(channel_id) REFERENCES channels(id));
CREATE TABLE IF NOT EXISTS streams (id TEXT PRIMARY KEY, url TEXT NOT NULL, headers TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memberships (channel_id TEXT, scope TEXT, position INTEGER,
 enabled INTEGER NOT NULL, available INTEGER NOT NULL, PRIMARY KEY(channel_id,scope));
CREATE TABLE IF NOT EXISTS routes (channel_id TEXT, scope TEXT, stream_id TEXT,
 rank INTEGER NOT NULL, priority INTEGER, enabled INTEGER NOT NULL, eligible INTEGER NOT NULL,
 evidence TEXT NOT NULL, last_seen TEXT NOT NULL, PRIMARY KEY(channel_id,scope,stream_id));
CREATE TABLE IF NOT EXISTS origins (stream_id TEXT, source TEXT, first_seen TEXT, last_seen TEXT,
 PRIMARY KEY(stream_id,source));
CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY, channel_id TEXT,
 stream_id TEXT, kind TEXT, ok INTEGER, reason TEXT, policy TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, action TEXT, target TEXT,
 scope TEXT, before_state TEXT, after_state TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, kind TEXT, revision INTEGER,
 state TEXT, error TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS versions (id TEXT PRIMARY KEY, revision INTEGER, state TEXT,
 manifest TEXT NOT NULL, error TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS version_states (version_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS backups (id TEXT PRIMARY KEY, state TEXT, object_key TEXT,
 sha256 TEXT, error TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS observations_by_channel ON observations(channel_id,id);
CREATE INDEX IF NOT EXISTS routes_by_stream ON routes(stream_id);
'''
SCOPES = ('main', 'live', 'english')


class Conflict(ValueError):
    pass


def configured_path(cfg):
    settings = cfg.get('channel_library', {})
    if not settings.get('enabled'):
        return None
    return Path(settings.get('path') or Path(cfg['data_dir']) / 'library/catalog.sqlite3')


class Library:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != 'nt':
            self.path.parent.chmod(0o700)
        with self.connect() as db:
            db.executescript(SCHEMA)
            db.execute("INSERT OR IGNORE INTO meta VALUES ('revision','0')")
            db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version','1')")
        if os.name != 'nt':
            self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def get_meta(db, key, default=None):
        row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def put_meta(db, key, value):
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))

    def revision(self):
        with self.connect() as db:
            return self.get_meta(db, 'revision', 0)

    def _bump(self, db):
        revision = self.get_meta(db, 'revision', 0) + 1
        self.put_meta(db, 'revision', revision)
        return revision

    def ingest(self, scope, text, source, evidence=None, report=None):
        if scope not in SCOPES:
            raise ValueError('invalid_scope')
        entries = parse_playlist(text)
        now = utcnow()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE memberships SET available=0 WHERE scope=?', (scope,))
            db.execute('UPDATE routes SET eligible=0 WHERE scope=?', (scope,))
            seen, positions, ranks = set(), {}, collections.Counter()
            for entry in entries:
                attrs = dict(ATTR.findall(entry['info']))
                # IDs persist across title/row changes. Name-based aliases remain
                # scoped and are only a matching aid, never the primary key.
                alias = ('epg:' + attrs['tvg-id'] + ':' + entry['key']) if attrs.get('tvg-id') else 'name:' + entry['key']
                known = db.execute('SELECT channel_id FROM aliases WHERE scope=? AND alias=?', (scope, alias)).fetchone()
                if known and attrs.get('tvg-id'):
                    existing = db.execute('SELECT metadata FROM channels WHERE id=?',(known[0],)).fetchone()
                    old_key = json.loads(existing[0]).get('_source_key')
                    same_request = db.execute('SELECT 1 FROM routes WHERE channel_id=? AND stream_id=?',
                                              (known[0],entry['endpoint']['id'])).fetchone()
                    if old_key and old_key != entry['key'] and not same_request:
                        alias += '|' + entry['key']
                        known = db.execute('SELECT channel_id FROM aliases WHERE scope=? AND alias=?',(scope,alias)).fetchone()
                if known:
                    cid = known[0]
                else:
                    requests = db.execute('SELECT DISTINCT r.channel_id,r.scope,c.metadata FROM routes r JOIN channels c ON c.id=r.channel_id WHERE stream_id=?',
                                          (entry['endpoint']['id'],)).fetchall()
                    matches=set()
                    for candidate in requests:
                        meta=json.loads(candidate['metadata'])
                        same_name=meta.get('_source_key')==entry['key']
                        plausible_rename=(candidate['scope']==scope and candidate['channel_id'] not in positions
                            and attrs.get('tvg-id') and meta.get('tvg-id')==attrs['tvg-id'])
                        if same_name or plausible_rename:matches.add(candidate['channel_id'])
                    cid = next(iter(matches)) if len(matches) == 1 else uuid.uuid4().hex
                    db.execute('INSERT OR IGNORE INTO channels VALUES (?,?,?,?,?,?,?)',
                               (cid, entry['name'], entry['group'], 'active', 1, now, json.dumps(dict(attrs,_source_key=entry['key']))))
                    db.execute('INSERT INTO aliases VALUES (?,?,?)', (scope, alias, cid))
                sid = entry['endpoint']['id']
                if (cid, sid) in seen:
                    continue
                seen.add((cid, sid))
                positions.setdefault(cid, len(positions))
                db.execute('INSERT OR IGNORE INTO streams VALUES (?,?,?)',
                           (sid, entry['endpoint']['url'], json.dumps(entry['endpoint']['headers'])))
                db.execute('INSERT OR IGNORE INTO memberships VALUES (?,?,?,?,?)',
                           (cid, scope, positions[cid], 1, 1))
                db.execute('UPDATE memberships SET position=?,available=1 WHERE channel_id=? AND scope=?',
                           (positions[cid], cid, scope))
                record = (evidence or {}).get(sid, {})
                label = record.get('validation_tier', 'imported_unverified')
                db.execute('INSERT OR IGNORE INTO routes VALUES (?,?,?,?,?,?,?,?,?)',
                           (cid, scope, sid, ranks[cid], None, 1, 1, label, now))
                db.execute('UPDATE routes SET rank=?,eligible=1,evidence=?,last_seen=? '
                           'WHERE channel_id=? AND scope=? AND stream_id=?',
                           (ranks[cid], label, now, cid, scope, sid))
                ranks[cid] += 1
                db.execute('INSERT OR IGNORE INTO origins VALUES (?,?,?,?)', (sid, source.split(':',1)[0] if source.startswith('qa:') else source, now, now))
                db.execute('UPDATE origins SET last_seen=? WHERE stream_id=? AND source=?', (now, sid, source.split(':',1)[0] if source.startswith('qa:') else source))
                for origin in record.get('sources', []):
                    db.execute('INSERT OR IGNORE INTO origins VALUES (?,?,?,?)',(sid,origin,now,now))
                    db.execute('UPDATE origins SET last_seen=? WHERE stream_id=? AND source=?',(now,sid,origin))
                if record:
                    db.execute('INSERT INTO observations VALUES (NULL,?,?,?,?,?,?,?)',
                        (cid, sid, 'detector', int(bool(record.get('ok'))), record.get('stability_error') or record.get('error', ''),
                         record.get('validation_policy', 'tiered-v1'), now))
            if report is not None:
                self.put_meta(db, 'report:' + scope, report)
            revision = self._bump(db)
            db.execute('INSERT INTO audit VALUES (NULL,?,?,?,?,?,?)',
                       ('import', source, scope, '{}', json.dumps({'channels': len(positions), 'lines': len(seen)}), now))
        return {'revision': revision, 'channels': len(positions), 'lines': len(seen)}

    def snapshot(self):
        with self.connect() as db:
            db.execute('BEGIN')
            result = {table: [dict(row) for row in db.execute('SELECT * FROM ' + table)]
                      for table in ('channels', 'memberships', 'routes', 'streams')}
            result['revision'] = self.get_meta(db, 'revision', 0)
            result['reports'] = {scope: self.get_meta(db, 'report:' + scope, {}) for scope in SCOPES}
            return result

    @staticmethod
    def view_snapshot(snapshot, scope, include_removed=False):
        members = {m['channel_id']: m for m in snapshot['memberships'] if m['scope'] == scope}
        streams = {r['id']: r for r in snapshot['streams']}
        routes = collections.defaultdict(list)
        for row in snapshot['routes']:
            if row['scope'] == scope:
                line = dict(row, url=streams[row['stream_id']]['url'],
                            headers=json.loads(streams[row['stream_id']]['headers']))
                routes[row['channel_id']].append(line)
        result = []
        for row in snapshot['channels']:
            member = members.get(row['id'])
            if not member or (not include_removed and
                (row['state'] != 'active' or not member['enabled'] or not member['available'])):
                continue
            lines = sorted(routes[row['id']], key=lambda r:
                (r['priority'] if r['priority'] is not None else 10000 + r['rank'], r['stream_id']))
            if not include_removed:
                lines = [r for r in lines if r['enabled'] and r['eligible']][:5]
                if not lines:
                    continue
            result.append(dict(row, scope=scope, position=member['position'],
                               enabled=member['enabled'], available=member['available'], lines=lines))
        return sorted(result, key=lambda r: (r['position'], r['id']))

    def view(self, scope, include_removed=False):
        if scope not in SCOPES:
            raise ValueError('invalid_scope')
        return self.view_snapshot(self.snapshot(), scope, include_removed)

    def admin_state(self, scope, query='', removed=False, page=0):
        snapshot = self.snapshot()
        rows = self.view_snapshot(snapshot, scope, True)
        rows = [r for r in rows if (not removed or not r['enabled'] or r['state'] != 'active')
                and query.casefold() in (r['name'] + ' ' + r['group_name']).casefold()]
        count = len(rows)
        for row in rows:
            for line in row['lines']:
                line['host'] = urlsplit(line.pop('url')).hostname
                line.pop('headers')
        with self.connect() as db:
            jobs = [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY created_at DESC,rowid DESC LIMIT 12')]
            versions = [dict(r) for r in db.execute('SELECT id,revision,state,error,created_at FROM versions ORDER BY rowid DESC LIMIT 15')]
            backups = [dict(r) for r in db.execute('SELECT * FROM backups ORDER BY rowid DESC LIMIT 10')]
            audit = [dict(r) for r in db.execute('SELECT action,scope,created_at FROM audit ORDER BY id DESC LIMIT 15')]
            for row in rows[page*50:(page+1)*50]:
                row['feedback'] = [dict(r) for r in db.execute('SELECT kind,ok,reason,policy,created_at FROM observations WHERE channel_id=? ORDER BY id DESC LIMIT 8',(row['id'],))]
                for line in row['lines']:
                    line['sources'] = [r[0] for r in db.execute('SELECT source FROM origins WHERE stream_id=? ORDER BY last_seen DESC LIMIT 8',(line['stream_id'],))]
        return dict(revision=snapshot['revision'], channels=rows[page*50:(page+1)*50], count=count,
                    page=page, jobs=jobs, versions=versions, backups=backups, audit=audit)

    def observe_detector(self, outcomes, policy):
        with self.connect() as db:
            mapping = {r['stream_id']:r['channel_id'] for r in db.execute('SELECT channel_id,stream_id FROM routes')}
            now=utcnow()
            db.executemany('INSERT INTO observations VALUES (NULL,?,?,?,?,?,?,?)',[
                (mapping.get(sid),sid,'detector',int(bool(r.get('ok'))),
                 r.get('stability_error') or r.get('error',''),policy,now) for sid,r in outcomes.items()])
            db.execute("DELETE FROM observations WHERE kind='detector' AND id NOT IN (SELECT id FROM observations WHERE kind='detector' ORDER BY id DESC LIMIT 100000)")

    def summary(self):
        snapshot = self.snapshot()
        counts = {scope: len(self.view_snapshot(snapshot,scope)) for scope in SCOPES}
        with self.connect() as db:
            published = self.get_meta(db,'published_revision',0)
            version = db.execute('SELECT state,created_at FROM versions ORDER BY rowid DESC LIMIT 1').fetchone()
            backup = db.execute('SELECT state,created_at FROM backups ORDER BY rowid DESC LIMIT 1').fetchone()
        return dict(counts, revision=snapshot['revision'],published_revision=published,
                    removed=sum(not m['enabled'] for m in snapshot['memberships']),
                    publication=dict(version) if version else None,backup=dict(backup) if backup else None)

    def preview(self, scope, cid):
        rows = [r for r in self.view(scope, True) if r['id'] == cid]
        if not rows:
            raise ValueError('unknown_channel')
        row = rows[0]
        outputs = {'main':['Top5','Top3','Set1','Set2','Set3','Stable'],
                   'live':['Live'], 'english':['English']}[scope]
        return {'revision':self.revision(), 'channel_id':cid, 'name':row['name'],
                'scope':scope, 'lines':len(row['lines']), 'outputs':outputs}

    def act(self, op, cid, scope, expected, value=None, sid=None, reason='', publish=False):
        if scope not in SCOPES or op not in ('quarantine', 'restore', 'disable_line', 'enable_line', 'priority', 'rename', 'group'):
            raise ValueError('invalid_operation')
        if not isinstance(expected, int) or isinstance(expected, bool):
            raise ValueError('revision_required')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if expected != self.get_meta(db, 'revision', 0):
                raise Conflict('stale_revision')
            member = db.execute('SELECT * FROM memberships WHERE channel_id=? AND scope=?', (cid, scope)).fetchone()
            if not member:
                raise ValueError('unknown_channel')
            if op in ('quarantine', 'restore'):
                before = dict(member)
                db.execute('UPDATE memberships SET enabled=? WHERE channel_id=? AND scope=?',
                           (int(op == 'restore'), cid, scope))
            elif op in ('disable_line', 'enable_line', 'priority'):
                row = db.execute('SELECT * FROM routes WHERE channel_id=? AND scope=? AND stream_id=?', (cid, scope, sid)).fetchone()
                if not row:
                    raise ValueError('unknown_line')
                before = dict(row)
                if op == 'priority':
                    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 999:
                        raise ValueError('invalid_priority')
                    db.execute('UPDATE routes SET priority=? WHERE channel_id=? AND scope=? AND stream_id=?', (value, cid, scope, sid))
                else:
                    db.execute('UPDATE routes SET enabled=? WHERE channel_id=? AND scope=? AND stream_id=?', (int(op == 'enable_line'), cid, scope, sid))
            else:
                if not isinstance(value, str) or not value.strip() or len(value) > 150 or any(c in value for c in '\r\n"'):
                    raise ValueError('invalid_text')
                before = dict(db.execute('SELECT * FROM channels WHERE id=?', (cid,)).fetchone())
                column = 'name' if op == 'rename' else 'group_name'
                db.execute('UPDATE channels SET ' + column + '=? WHERE id=?', (value.strip(), cid))
            db.execute('UPDATE channels SET revision=revision+1,updated_at=? WHERE id=?', (utcnow(), cid))
            revision = self._bump(db)
            db.execute('INSERT INTO audit VALUES (NULL,?,?,?,?,?,?)',
                (op, cid, scope, json.dumps(before), json.dumps({'value': value, 'line': sid, 'reason': reason[:100]}), utcnow()))
            if reason:
                db.execute('INSERT INTO observations VALUES (NULL,?,?,?,?,?,?,?)',
                    (cid, sid, 'browser', 0, reason[:100], 'startup-20s', utcnow()))
            if publish:
                self._enqueue(db, 'publish', revision)
        return revision

    @staticmethod
    def _enqueue(db, kind, revision):
        identity = uuid.uuid4().hex
        db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)', (identity, kind, revision, 'pending', '', utcnow(), utcnow()))
        return identity

    def enqueue(self, kind, expected):
        if kind not in ('publish', 'backup'):
            raise ValueError('invalid_job')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            revision = self.get_meta(db, 'revision', 0)
            if revision != expected:
                raise Conflict('stale_revision')
            return self._enqueue(db, kind, revision)

    def rollback(self, state, expected):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if expected != self.get_meta(db, 'revision', 0):
                raise Conflict('stale_revision')
            db.execute('UPDATE memberships SET enabled=0')
            db.execute('UPDATE routes SET enabled=0')
            for r in state['channels']:
                db.execute('UPDATE channels SET name=?,group_name=?,state=?,revision=revision+1,updated_at=? WHERE id=?',
                    (r['name'], r['group_name'], r['state'], utcnow(), r['id']))
            for r in state['memberships']:
                db.execute('UPDATE memberships SET enabled=? WHERE channel_id=? AND scope=?', (r['enabled'], r['channel_id'], r['scope']))
            for r in state['routes']:
                db.execute('UPDATE routes SET enabled=?,priority=? WHERE channel_id=? AND scope=? AND stream_id=?',
                    (r['enabled'], r['priority'], r['channel_id'], r['scope'], r['stream_id']))
            revision = self._bump(db)
            db.execute('INSERT INTO audit VALUES (NULL,?,?,?,?,?,?)', ('rollback', '', '', '{}', json.dumps({'from_revision': state['revision']}), utcnow()))
            self._enqueue(db, 'publish', revision)
        return revision

    def snapshot_db(self, target):
        # sqlite backup() is available on Python >=3.7; legacy Python uses
        # SQLite's consistent SQL dump inside one read transaction instead.
        with self.connect() as db:
            out = sqlite3.connect(str(target))
            try:
                if hasattr(db, 'backup'):
                    db.backup(out)
                else:
                    db.execute('BEGIN')
                    out.executescript('\n'.join(db.iterdump()))
                if out.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('snapshot_integrity_failed')
            finally:
                out.close()
