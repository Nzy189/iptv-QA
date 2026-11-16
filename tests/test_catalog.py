import json
import unittest
from unittest import mock
from pathlib import Path
import types
import os

import catalog_sources as catalog
import iptv_checker as app


class CatalogTests(unittest.TestCase):
    def test_radio_policy_excludes_station_even_with_possible_cover_video(self):
        for name, attributes in [('北京新闻广播', ''), ('FM 97.4', ''), ('CNR1', ''),
                                  ('BBC Radio 4', ''), ('Station', 'radio="true"')]:
            entry=self.entry(name,attributes)
            self.assertTrue(catalog.is_radio(entry),name)
            self.assertEqual(catalog.classify(entry,{}),'excluded_radio',name)
        for name in ('TVB翡翠台', '电视广播有限公司', '中国教育电视台', '音乐电视', 'CCTV15'):
            self.assertFalse(catalog.is_radio(self.entry(name)),name)
    def entry(self, name, attributes=''):
        return app.parse_playlist('#EXTM3U\n#EXTINF:-1 '+attributes+','+name+'\nhttp://public.test/live\n')[0]

    def test_chinese_english_other_and_unknown(self):
        for name, expected in [('湖南卫视高清','zh'),('凤凰中文台','zh'),('BBC One','en'),('CNN International','en'),('NHK総合','excluded_language'),('日本テレビ','excluded_language'),('BBC Arabic','excluded_language'),('UnknownLatinName','unknown_language')]:
            self.assertEqual(catalog.classify(self.entry(name),{}), expected, name)

    def test_language_registry_overrides_alphabet_and_detects_nsfw(self):
        db={'Example.sg':{'languages':['zho'],'country':'SG'},'Example.fr':{'languages':['fra'],'country':'FR'},'Blocked.us':{'languages':['eng'],'is_nsfw':True}}
        self.assertEqual(catalog.classify(self.entry('Example','tvg-id="Example.sg"'),db),'zh')
        self.assertEqual(catalog.classify(self.entry('Example','tvg-id="Example.fr"'),db),'excluded_language')
        self.assertEqual(catalog.classify(self.entry('Blocked','tvg-id="Blocked.us"'),db),'excluded_nsfw')

    def test_country_and_explicit_language(self):
        self.assertEqual(catalog.classify(self.entry('Channel One','tvg-country="US"'),{}),'en')
        self.assertEqual(catalog.classify(self.entry('Channel One','tvg-language="Chinese"'),{}),'zh')
        self.assertEqual(catalog.classify(self.entry('Channel One','tvg-language="Spanish"'),{}),'excluded_language')

    def test_quality_aliases_merge_existing_channel(self):
        self.assertEqual(app.normalize('湖南卫视(1080p)'),app.normalize('湖南卫视'))
        self.assertEqual(app.normalize('凤凰中文台'),app.normalize('凤凰中文'))

    def test_unroutable_sources_are_classified_without_network_probe(self):
        for url, expected in [('http://192.168.1.1/live','local_network_unreachable'),('udp://239.1.1.1:1234','multicast_unreachable'),('http://[2400:3200::1]/live','ipv6_unavailable')]:
            with mock.patch.object(app,'run_tool') as tool:
                result=app.probe(app.endpoint(url,[]),app.UA,{'skip_unroutable':True,'ipv6_available':False})
                self.assertEqual(result['error'],expected)
                tool.assert_not_called()

    def test_duplicate_sources_are_credited_and_best_lines_shared(self):
        old=self.entry('CCTV1');old['source']='seller'
        new=self.entry('CCTV1');new['source']='tvlist_zh'
        channels,endpoints=app.merge_entries([[old],[new]])
        i=old['endpoint']['id'];decode={i:{'ok':True,'seconds':1}};probe={i:{'height':1080,'seconds':1}}
        outputs,stats=app.render_playlists(channels,probe,decode,{})
        report=app.source_comparison(channels,endpoints,decode,outputs)
        self.assertEqual(report['overlap_channels'],1)
        self.assertEqual(report['repository_best_channels'],0)
        self.assertTrue(all(r['top3_entries']==1 for r in report['sources']))

    def test_english_upload_produces_one_object(self):
        client=mock.Mock();client.putFile.return_value.status=200
        sdk=types.SimpleNamespace(ObsClient=mock.Mock(return_value=client),PutObjectHeader=types.SimpleNamespace)
        with mock.patch.dict('sys.modules',{'obs':sdk,'certifi':types.SimpleNamespace(where=lambda:'ca.pem')}),mock.patch.dict(os.environ,{'OBS_ACCESS_KEY_ID':'test','OBS_SECRET_ACCESS_KEY':'test'}):
            result=app.publish_obs(Path('/unused'),{'enabled':True,'endpoint':'https://obs.example.test','bucket':'example-bucket','prefix':'','upload_report':False,'playlist_files':['iptv_top3.m3u'],'object_names':{'iptv_top3.m3u':'iptv_english.m3u'}})
        self.assertEqual(result['objects'],['iptv_english.m3u'])


if __name__=='__main__':unittest.main(verbosity=2)
