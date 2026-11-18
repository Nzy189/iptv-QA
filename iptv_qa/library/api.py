"""Management operations behind the player's existing authentication boundary."""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .store import Conflict


class LibraryAPI:
    def __init__(self, library):
        self.library = library

    def get(self, handler, player):
        if urlsplit(handler.path).path == '/api/library/status':
            return self.library.summary()
        query = parse_qs(urlsplit(handler.path).query)
        scope = query.get('scope',['main'])[0]
        page = max(0,min(100000,int(query.get('page',['0'])[0])))
        return self.library.admin_state(scope,query.get('q',[''])[0][:150],
                                        query.get('removed',['0'])[0]=='1',page)

    def post(self, handler, player):
        origin = handler.headers.get('Origin')
        if origin and urlsplit(origin).netloc != handler.headers.get('Host'):
            raise ValueError('cross_origin_rejected')
        if not handler.headers.get('Content-Type','').startswith('application/json'):
            raise ValueError('json_required')
        body = player.read_json(handler)
        if not isinstance(body,dict):
            raise ValueError('bad_request')
        operation = body.get('operation')
        if operation in ('publish','backup'):
            return {'job':self.library.enqueue(operation,body.get('revision'))}
        if operation == 'rollback':
            identity = body.get('version','')
            with self.library.connect() as db:
                row = db.execute('SELECT id FROM versions WHERE id=?',(identity,)).fetchone()
            if not row:
                raise ValueError('unknown_version')
            with self.library.connect() as db:
                saved=db.execute('SELECT payload FROM version_states WHERE version_id=?',(identity,)).fetchone()
            state = json.loads(saved[0]) if saved else json.loads((self.library.path.parent/'versions'/identity/'state.json').read_text(encoding='utf-8'))
            return {'revision':self.library.rollback(state,body.get('revision'))}
        if operation == 'cleanup':
            token=player.session_token(handler)
            source_id=body.get('source_id','')
            session = player.sessions.get(token+':'+source_id) if isinstance(source_id,str) else None
            if not session or not session.get('library_scope') or body.get('source_id') != session.get('source_id'):
                raise Conflict('stale_player_source')
            cid = body.get('channel_id')
            if cid not in {c['id'] for c in session['channels']}:
                raise ValueError('unknown_channel')
            if body.get('reason') not in ('startup_timeout','all_lines_failed'):
                raise ValueError('invalid_reason')
            revision = self.library.act('quarantine',cid,session['library_scope'],
                session['library_revision'],reason=body['reason'],publish=True)
            session['library_revision'] = revision
            return {'revision':revision,'state':'pending','channel_id':cid}
        if operation == 'preview':
            return self.library.preview(body.get('scope'),body.get('channel_id'))
        revision = self.library.act(operation,body.get('channel_id'),body.get('scope'),
            body.get('revision'),body.get('value'),body.get('stream_id'))
        return {'revision':revision,'state':'unpublished'}
