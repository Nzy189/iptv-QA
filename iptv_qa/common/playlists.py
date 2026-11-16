"""M3U parsing and identity, independent of detection and playback."""
import collections
import hashlib
import json
import re
import unicodedata
from urllib.parse import parse_qsl, urlsplit

ATTR = re.compile(r'([\w-]+)="([^"]*)"')
ALLOWED_SCHEMES = {"http", "https", "rtsp", "rtmp", "rtmps", "rtmpt", "rtmpts", "udp", "rtp", "srt"}


def normalize(name):
    name = unicodedata.normalize('NFKC', name).upper()
    name = re.sub(r'[\s_-]+', '', name)
    name = re.sub(r'[\[(](?:1080P|720P|2160P|HD|FHD|UHD|HEVC|H264|H265|高清|超高清|标清|蓝光)[\])]', '', name)
    name = re.sub(r'(?:超高清|高清|标清|蓝光|HD|FHD|UHD|1080P|720P|HEVC|H265|频道)+$', '', name)
    match = re.match(r'^CCTV(4K|8K|\d{1,2}\+?)(.*)$', name)
    if match:
        number, suffix = match.groups()
        # Keep regional/time-shifted editions separate.
        editions = [edition for edition in ('欧洲', '美洲', '亚洲', 'EUROPE', 'AMERICA', 'ASIA')
                    if edition in suffix]
        return 'CCTV' + number + ''.join(editions)
    return {'凤凰中文台': '凤凰中文', '凤凰卫视中文台': '凤凰中文', '凤凰资讯台': '凤凰资讯',
            '凤凰卫视资讯台': '凤凰资讯', '凤凰香港台': '凤凰香港'}.get(name, name)

def split_extinf(line):
    quoted = False
    for index, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == ',' and not quoted:
            return line[:index], line[index + 1:].strip()
    raise ValueError('EXTINF has no channel-name delimiter')

def endpoint(url, extra):
    raw_url, sep, options = url.partition('|')
    headers = {}
    for line in extra:
        if line.lower().startswith('#extvlcopt:http-user-agent='):
            headers['user_agent'] = line.split('=', 1)[1]
        if line.lower().startswith(('#extvlcopt:http-referrer=', '#extvlcopt:http-referer=')):
            headers['referer'] = line.split('=', 1)[1]
    if sep:
        for key, value in parse_qsl(options, keep_blank_values=True):
            key = key.lower()
            if key in ('user-agent', 'user_agent'):
                headers['user_agent'] = value
            elif key in ('referer', 'referrer'):
                headers['referer'] = value
    if any('\r' in v or '\n' in v for v in headers.values()):
        raise ValueError('Invalid request header')
    parts = urlsplit(raw_url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES or not parts.hostname:
        raise ValueError('Unsupported stream URL')
    identity = json.dumps([raw_url, headers], sort_keys=True, ensure_ascii=False)
    return {'id': hashlib.sha256(identity.encode('utf-8')).hexdigest(),
            'url': raw_url, 'headers': headers, 'host': parts.hostname.lower()}

def parse_playlist(text):
    text = text.lstrip('\ufeff')
    if '<html' in text[:1024].lower() or '<!doctype html' in text[:1024].lower():
        raise ValueError('Source returned HTML instead of M3U')
    entries, pending, extra = [], None, []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith('#EXTINF:'):
            pending, extra = line, []
        elif line.startswith('#'):
            if pending and line.startswith(('#EXTVLCOPT:', '#KODIPROP:')):
                extra.append(line)
        elif pending:
            prefix, name = split_extinf(pending)
            attrs = dict(ATTR.findall(prefix))
            group = attrs.get('group-title', '')
            if name and not any(marker in group + name for marker in ('联系客服', '更新时间')):
                try:
                    ep = endpoint(line, extra)
                except ValueError:
                    pending, extra = None, []
                    continue
                entries.append({'key': normalize(name), 'name': name, 'group': group,
                                'info': pending, 'extra': list(extra), 'output_url': line,
                                'endpoint': ep})
            pending, extra = None, []
    if not entries:
        raise ValueError('No valid EXTINF/URL pairs found')
    return entries

def merge_entries(playlists):
    channels = collections.OrderedDict()
    endpoints = {}
    for entries in playlists:
        for entry in entries:
            key, ep = entry['key'], entry['endpoint']
            if key not in channels:
                channels[key] = {'name': entry['name'], 'group': entry['group'],
                                 'info': entry['info'], 'entries': collections.OrderedDict()}
            channels[key]['entries'].setdefault(ep['id'], entry)
            sources = [entry.get('source', 'unknown')]
            canonical = channels[key]['entries'][ep['id']]
            canonical['sources'] = list(dict.fromkeys(canonical.get('sources', []) + sources))
            stored = endpoints.setdefault(ep['id'], ep)
            stored['sources'] = list(dict.fromkeys(stored.get('sources', []) + sources))
    return channels, endpoints
