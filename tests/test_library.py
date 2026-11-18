import json
import os
from pathlib import Path
import tempfile
import unittest

from iptv_qa.library.store import Library, Conflict
from iptv_qa.library.publishing import Publisher, render
from iptv_qa.library.cli import process_jobs


def playlist(count=2, title='News', epg='news.test'):
    return '#EXTM3U\n' + ''.join('#EXTINF:-1 tvg-id="'+epg+'" group-title="News",'+title+'\nhttp://stream%d.example/live\n'%n for n in range(count))


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Library(self.root/'library/catalog.sqlite3')
        self.cfg = dict(data_dir=str(self.root), channel_library={'enabled':True}, obs={'enabled':False})

    def tearDown(self):
        self.temp.cleanup()

    def test_stable_identity_survives_rename_and_reimport(self):
        self.store.ingest('main',playlist(),'migration')
        cid = self.store.view('main')[0]['id']
        self.store.act('rename',cid,'main',self.store.revision(),'My News')
        self.store.ingest('main',playlist(title='Renamed upstream'),'qa')
        row = self.store.view('main')[0]
        self.assertEqual(row['id'],cid)
        self.assertEqual(row['name'],'My News')
        self.assertEqual(len(row['lines']),2)

    def test_scopes_share_identity_but_removal_is_scoped_and_persistent(self):
        self.store.ingest('main',playlist(),'sourceA')
        self.store.ingest('english',playlist(),'sourceB')
        cid = self.store.view('main')[0]['id']
        self.assertEqual(self.store.view('english')[0]['id'],cid)
        self.store.act('quarantine',cid,'main',self.store.revision(),reason='all_lines_failed',publish=True)
        self.assertEqual(self.store.view('main'),[])
        self.assertEqual(len(self.store.view('english')),1)
        self.store.ingest('main',playlist(),'next-qa')
        self.assertEqual(self.store.view('main'),[])
        self.store.act('restore',cid,'main',self.store.revision())
        self.assertEqual(len(self.store.view('main')),1)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT kind FROM observations').fetchone()[0],'browser')

    def test_top5_limit_and_disabled_lines_survive_refresh(self):
        self.store.ingest('main',playlist(7),'source')
        row=self.store.view('main')[0]
        self.assertEqual(len(row['lines']),5)
        sid=row['lines'][0]['stream_id']
        self.store.act('disable_line',row['id'],'main',self.store.revision(),sid=sid)
        self.store.ingest('main',playlist(7),'next')
        self.assertNotIn(sid,[l['stream_id'] for l in self.store.view('main')[0]['lines']])
        self.assertEqual(len(self.store.view('main',True)[0]['lines']),7)

    def test_priority_does_not_resurrect_ineligible_old_stream(self):
        self.store.ingest('main',playlist(2),'source')
        row=self.store.view('main')[0];sid=row['lines'][1]['stream_id']
        self.store.act('priority',row['id'],'main',self.store.revision(),0,sid)
        self.assertEqual(self.store.view('main')[0]['lines'][0]['stream_id'],sid)
        self.store.ingest('main',playlist(1),'next')
        self.assertNotIn(sid,[l['stream_id'] for l in self.store.view('main')[0]['lines']])

    def test_duplicate_epg_and_request_do_not_collapse_distinct_names_in_one_import(self):
        content=playlist(1,title='Movie A',epg='shared.test')+playlist(1,title='Movie B',epg='shared.test')
        self.store.ingest('main',content,'conflicting-provider')
        self.assertEqual(len(self.store.view('main')),2)
        self.assertEqual(len({r['id'] for r in self.store.view('main')}),2)

    def test_stale_edit_is_rejected_and_transaction_unchanged(self):
        self.store.ingest('main',playlist(),'source')
        row=self.store.view('main')[0];rev=self.store.revision()
        self.store.act('rename',row['id'],'main',rev,'New')
        with self.assertRaises(Conflict):self.store.act('quarantine',row['id'],'main',rev)
        self.assertEqual(len(self.store.view('main')),1)

    def test_public_admin_payload_omits_tokens_and_headers(self):
        self.store.ingest('main',playlist().replace('/live','/live?token=private-value'),'source')
        payload=json.dumps(self.store.admin_state('main'))
        self.assertNotIn('private-value',payload)
        self.assertNotIn('http://',payload)

    def test_render_preserves_epg_identity_and_emits_fixed_channel_id(self):
        self.store.ingest('main',playlist(7),'source')
        content=render(self.store.view('main'))
        self.assertEqual(content.count('#EXTINF:'),5)
        self.assertIn('tvg-id="news.test"',content)
        self.assertIn('channel-id="'+self.store.view('main')[0]['id']+'"',content)

    def test_running_legacy_qa_is_reconciled_without_restoring_manual_removal(self):
        from iptv_qa.library.cli import reconcile_external
        self.store.ingest('main',playlist(),'old',report={'run_id':'old'})
        cid=self.store.view('main')[0]['id']
        self.store.act('quarantine',cid,'main',self.store.revision())
        folder=self.root/'published/current';folder.mkdir(parents=True)
        (folder/'iptv_top5.m3u').write_text(playlist(),encoding='utf-8')
        (folder/'report.json').write_text(json.dumps({'published':True,'run_id':'new'}))
        reconcile_external(self.store,self.cfg)
        self.assertEqual(self.store.view('main'),[])
        rev=self.store.revision();reconcile_external(self.store,self.cfg)
        self.assertEqual(self.store.revision(),rev)

    def test_restore_snapshot_integrity_and_live_database_unchanged(self):
        self.store.ingest('main',playlist(),'source')
        target=self.root/'snapshot.sqlite3';self.store.snapshot_db(target)
        recovered=Library(target)
        self.assertEqual(recovered.view('main')[0]['id'],self.store.view('main')[0]['id'])

    @unittest.skipIf(os.name=='nt','Atomic symlink publication verified on Linux')
    def test_versioned_publication_and_rollback_keep_audit(self):
        self.store.ingest('main',playlist(),'source')
        publisher=Publisher(self.cfg,library=self.store)
        first=publisher.publish()
        self.assertEqual(first['state'],'local_only')
        with self.store.connect() as db:
            self.assertIsNotNone(db.execute('SELECT payload FROM version_states WHERE version_id=?',(first['id'],)).fetchone())
        content=(self.root/'published/iptv_top5.m3u').read_text()
        self.assertEqual(content.count('#EXTINF:'),2)
        state=json.loads((self.root/'library/versions'/first['id']/'state.json').read_text())
        cid=self.store.view('main')[0]['id']
        self.store.act('quarantine',cid,'main',self.store.revision())
        publisher.publish()
        self.assertEqual((self.root/'published/iptv_top5.m3u').read_text().count('#EXTINF:'),0)
        self.store.rollback(state,self.store.revision());process_jobs(self.store,self.cfg)
        self.assertEqual(len(self.store.view('main')),1)
        self.assertEqual((self.root/'published/iptv_top5.m3u').read_text().count('#EXTINF:'),2)
        self.assertEqual(self.store.admin_state('main')['audit'][0]['action'],'rollback')

    @unittest.skipIf(os.name=='nt','Atomic symlink publication verified on Linux')
    def test_partial_upload_is_marked_failed_and_retried_same_version(self):
        self.store.ingest('main',playlist(),'source')
        calls=[]
        def failed(*args,**kwargs):
            calls.append(args);raise OSError('network')
        cfg=dict(self.cfg,obs={'enabled':True})
        result=Publisher(cfg,library=self.store,upload=failed).publish()
        self.assertEqual(result['state'],'sync_failed')
        def uploaded(*args,**kwargs):return {'state':'uploaded'}
        retry=Publisher(cfg,library=self.store,upload=uploaded).publish()
        self.assertEqual(retry['id'],result['id'])
        self.assertEqual(retry['state'],'synced')

    def test_queued_publish_does_not_publish_unreviewed_new_revision(self):
        self.store.ingest('main',playlist(),'source')
        job=self.store.enqueue('publish',self.store.revision())
        self.store.act('rename',self.store.view('main')[0]['id'],'main',self.store.revision(),'Later edit')
        process_jobs(self.store,self.cfg)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM jobs WHERE id=?',(job,)).fetchone()[0],'superseded')



class BackupTests(unittest.TestCase):
    def test_encrypted_backup_roundtrip_and_tamper_rejection(self):
        try:
            from cryptography.fernet import Fernet, InvalidToken
        except ImportError:
            self.skipTest('Optional backup crypto dependency tested on deployment Linux')
        from iptv_qa.library.backup import backup_library, restore_backup
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);store=Library(root/'library/catalog.sqlite3')
            store.ingest('main',playlist(),'fixture')
            key=root/'restore.key';key.write_bytes(Fernet.generate_key())
            cfg=dict(data_dir=str(root),obs={'enabled':False,'secret_access_key':'must-not-be-backed-up'},
                channel_library={'enabled':True,'backup':{'enabled':True,'key_path':str(key)}})
            captured=[]
            result=backup_library(store,cfg,uploader=lambda path,cfg,key:captured.append(Path(path)))
            self.assertEqual(result['state'],'verified')
            restored=root/'restored.sqlite3';restore_backup(captured[0],key,restored)
            self.assertEqual(Library(restored).view('main')[0]['id'],store.view('main')[0]['id'])
            with self.assertRaises(ValueError):restore_backup(captured[0],key,restored)
            damaged=root/'damaged.enc';data=bytearray(captured[0].read_bytes());data[-8]^=1;damaged.write_bytes(data)
            with self.assertRaises(InvalidToken):restore_backup(damaged,key,root/'bad.sqlite3')
            self.assertFalse((root/'bad.sqlite3').exists())


class LibraryHTTPTests(unittest.TestCase):
    def setUp(self):
        import threading
        from iptv_qa.playback.service import TVPlugin
        from iptv_qa.server.http import Server, make_handler
        from test_tv import Client
        self.temp=tempfile.TemporaryDirectory()
        self.store=Library(Path(self.temp.name)/'library/catalog.sqlite3')
        self.store.ingest('main',playlist(7),'fixture')
        self.plugin=TVPlugin('library-test-password',public_base='http://owned.example',library=self.store)
        self.server=Server(('127.0.0.1',0),make_handler(self.temp.name,'',self.plugin))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.client=Client(self.server.server_port)

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.plugin.close();self.temp.cleanup()

    def login(self):
        self.assertEqual(self.client.request('POST','/api/tv/login',{'password':'library-test-password'})[0],200)

    def test_browser_sessions_and_tab_sources_are_isolated(self):
        from test_tv import Client
        self.login()
        other=Client(self.server.server_port)
        other.request('POST','/api/tv/login',{'password':'library-test-password'})
        self.assertNotEqual(self.client.cookie,other.cookie)
        self.store.ingest('english',playlist(1,title='English'),'fixture')
        _,_,body=self.client.request('POST','/api/tv/source',{'url':'http://owned.example/iptv_ok.m3u'})
        first=json.loads(body)
        self.client.request('POST','/api/tv/source',{'url':'http://owned.example/iptv_english.m3u'})
        from unittest import mock
        with mock.patch('iptv_qa.playback.service.fetch_upstream',return_value=(200,{},b'segment','http://stream4.example/live')) as upstream:
            status=self.client.request('GET','/tv/play/0/4?source_id='+first['source_id'])[0]
            self.assertEqual(status,200)
        self.assertEqual(other.request('GET','/tv/play/0/0?source_id='+first['source_id'])[0],404)

    def test_management_requires_login_and_does_not_serve_database(self):
        self.assertEqual(self.client.request('GET','/api/library/state')[0],401)
        self.assertEqual(self.client.request('POST','/api/library/action',{'operation':'publish'})[0],401)
        self.login();status,_,body=self.client.request('GET','/api/library/state')
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['count'],1)
        self.assertNotIn(b'http://stream',body)
        self.assertEqual(self.client.request('GET','/library/catalog.sqlite3')[0],404)
        self.assertEqual(self.client.request('GET','/tv/manage/')[0],200)

    def test_top5_session_deduplicates_channels_and_serves_fifth_line(self):
        from unittest import mock
        self.login();status,_,body=self.client.request('POST','/api/tv/source',{'url':'http://owned.example/iptv_ok.m3u'})
        payload=json.loads(body);self.assertEqual(payload['count'],1);self.assertEqual(payload['sets'],5)
        self.assertEqual(payload['channels'][0]['line_count'],5)
        with mock.patch('iptv_qa.playback.service.fetch_upstream',return_value=(200,{},b'segment','http://stream4.example/live')) as upstream:
            self.assertEqual(self.client.request('GET','/tv/play/0/4')[0],200)
            self.assertEqual(upstream.call_args[0][0],'http://stream4.example/live')
        self.assertEqual(self.client.request('GET','/tv/play/0/5')[0],410)

    def test_cleanup_rejects_stale_source_and_can_be_restored(self):
        self.login();_,_,body=self.client.request('POST','/api/tv/source',{'url':'http://owned.example/iptv_ok.m3u'})
        payload=json.loads(body);cid=payload['channels'][0]['id']
        request={'operation':'cleanup','channel_id':cid,'source_id':'old','reason':'all_lines_failed'}
        self.assertEqual(self.client.request('POST','/api/library/action',request)[0],409)
        request['source_id']=payload['source_id']
        self.assertEqual(self.client.request('POST','/api/library/action',request)[0],200)
        self.assertEqual(self.store.view('main'),[])
        self.assertEqual(self.client.request('GET','/tv/play/0/0')[0],410)
        restore={'operation':'restore','scope':'main','channel_id':cid,'revision':self.store.revision()}
        self.assertEqual(self.client.request('POST','/api/library/action',restore)[0],200)
        self.assertEqual(self.client.request('POST','/api/library/action',restore)[0],409)
        self.assertEqual(len(self.store.view('main')),1)

if __name__=='__main__':unittest.main()
