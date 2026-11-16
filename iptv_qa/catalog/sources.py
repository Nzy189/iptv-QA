"""Fetch public playlist data and route ordinary Chinese / English channels."""
import collections
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
import time
from urllib.request import Request, urlopen

ZH = {'zho', 'chi', 'cmn', 'yue', 'nan', 'hak', 'wuu', 'chinese', 'mandarin', 'cantonese', '中文', '汉语', '華語', '华语', '粤语'}
EN = {'eng', 'en', 'english', '英语', '英語'}
ADULT = re.compile(r'\b(?:adult|xxx|porn|erotic|nsfw)\b|18\+|成人|色情|无码|無碼', re.I)
EN_NETWORK = re.compile(r'^(BBC|CNN|CNBC|MSNBC|BLOOMBERG|SKYNEWS|SKY NEWS|FOXNEWS|FOX NEWS|ABCNEWS|ABC NEWS|CBSNEWS|CBS NEWS|NBCNEWS|NBC NEWS|ALJAZEERAENGLISH|AL JAZEERA ENGLISH|NHKWORLD|NHK WORLD|TRTWORLD|TRT WORLD|CGTN(?:\s|$)|CCTVNEWS|WION|NDTV24)', re.I)
OTHER_LANGUAGE = re.compile(r'日本|日语|日語|韩国|韓國|韩语|韓語|俄语|俄語|法语|法語|西班牙|葡萄牙|阿拉伯|العربية|FRAN[ÇC]AIS|ESPA[NÑ]OL|RUSSIAN|ARABIC|PORTUGU[EÊ]S|^NHK(?!WORLD| WORLD)|^KBS|^MBC|^EBS|^BS[0-9]|富士|テレビ|朝日|読売', re.I)


def attrs(entry):
    return dict(re.findall(r'([\w-]+)="([^"]*)"', entry['info']))


def is_radio(entry):
    """Exclude explicit stations, without classifying TVB or music TV as radio."""
    a = attrs(entry)
    if a.get('radio', '').lower() in ('true', '1', 'yes') or a.get('tvg-type', '').lower() == 'radio':
        return True
    name, group = entry['name'].strip(), entry.get('group', '').strip()
    if re.search(r'广播电视|廣播電視|电视广播|電視廣播', name):
        return False
    if re.search(r'电台|電台|广播|廣播|\bradio\b|\bFM\s*\d|^CNR\s*\d', name, re.I):
        return True
    if not re.search(r'电视|電視|\bTV\b', group, re.I) and re.search(r'广播|廣播|电台|電台|\bradio\b|^FM$', group, re.I):
        return True
    return False


def classify(entry, database):
    a = attrs(entry)
    channel = database.get(a.get('tvg-id', ''), {})
    label = entry['name'] + ' ' + entry.get('group', '')
    if channel.get('is_nsfw') or ADULT.search(label):
        return 'excluded_nsfw'
    if is_radio(entry):
        return 'excluded_radio'
    language = a.get('tvg-language', '') or channel.get('languages', []) or []
    if isinstance(language, str):
        language = re.split(r'[,;/|\s]+', language)
    languages = {str(v).strip().lower() for v in language if str(v).strip()}
    if languages & ZH:
        return 'zh'
    if languages & EN:
        return 'en'
    if languages:
        return 'excluded_language'
    name = entry['name'].strip()
    if re.search(r'CGTN.*(?:FRAN|ESPA|ARAB|RUSS|法|西|阿|俄)|CCTV.*(?:法语|法語|西语|西語|阿语|阿語|俄语|俄語)', name, re.I):
        return 'excluded_language'
    if re.search(r'ARABIC|RUSSIAN|PERSIAN|URDU|HINDI|BENGALI|TAMIL|PORTUGUESE|FRAN[ÇC]AIS|ESPA[NÑ]OL|阿语|阿語|俄语|俄語|法语|法語|韩语|韓語', name, re.I):
        return 'excluded_language'
    if EN_NETWORK.search(name) or re.search(r'\bEnglish\b|英语|英語', name, re.I):
        return 'en'
    country = a.get('tvg-country', '') or channel.get('country', '')
    if not country:
        m = re.search(r'\.([a-zA-Z]{2})$', a.get('tvg-id', ''))
        country = m.group(1) if m else ''
    country = country.upper()
    if country in ('CN', 'HK', 'TW', 'MO'):
        return 'zh'
    if country in ('US', 'GB', 'UK', 'AU', 'NZ', 'IE'):
        return 'en'
    if country and country not in ('SG', 'MY', 'CA'):
        return 'excluded_language'
    if re.search(r'^(?:CCTV[- ]?\d|CETV|CNR|BRTV|BTV|TVBS)|中文|華語|华语|凤凰|鳳凰|中天|民視|民视|三立|東森|东森|緯來|纬来|台視|台视|華視|华视|中視|中视|公視|公视', name, re.I):
        return 'zh'
    if OTHER_LANGUAGE.search(label) or re.search(r'[\u3040-\u30ff\uac00-\ud7af]', name):
        return 'excluded_language'
    if re.search(r'[\u4e00-\u9fff]', name):
        return 'zh'
    return 'unknown_language'


def fetch(url, limit=32*1024*1024):
    last = None
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={'User-Agent': 'IPTV-Catalog-Checker/1.0'}), timeout=45) as response:
                data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError('Catalog response exceeds size limit')
            return data
        except Exception as exc:
            last = type(exc).__name__
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError('Catalog download failed: ' + str(last))


def decode(data):
    try:
        return data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return data.decode('gb18030', 'replace')


def playlist(entries):
    lines = ['#EXTM3U']
    seen = set()
    for entry in entries:
        identity = (entry['key'], entry['endpoint']['id'])
        if identity in seen:
            continue
        seen.add(identity)
        lines.extend([entry['info']] + entry['extra'] + [entry['output_url']])
    return '\n'.join(lines) + '\n'


def synchronize(cfg, parse, normalize, write_json, atomic_text, progress=None):
    catalog = cfg.get('catalog', {})
    data = Path(cfg['data_dir'])
    root = data / 'catalog'
    root.mkdir(parents=True, exist_ok=True)
    cache = data / 'catalog-cache'
    cache.mkdir(exist_ok=True)
    if progress:
        progress('catalog_download')
    stale = False
    try:
        archive_bytes = fetch(catalog.get('archive_url', 'https://codeload.github.com/imDazui/Tvlist-awesome-m3u-m3u8/tar.gz/refs/heads/master'))
        (cache / 'repository.tar.gz').write_bytes(archive_bytes)
    except RuntimeError:
        candidates = [cache / 'repository.tar.gz', data / 'catalog-audit/repository.tar.gz']
        existing = next((p for p in candidates if p.exists()), None)
        if not existing:
            raise
        archive_bytes = existing.read_bytes()
        stale = True
    database = {}
    try:
        metadata = fetch(catalog.get('metadata_url', 'https://iptv-org.github.io/api/channels.json'), 32*1024*1024)
        channels = json.loads(metadata.decode('utf-8'))
        database = {c['id']: c for c in channels if isinstance(c, dict) and c.get('id')}
        (cache / 'language-metadata.json').write_bytes(metadata)
    except Exception:
        if (cache / 'language-metadata.json').exists():
            database = {c['id']: c for c in json.loads((cache / 'language-metadata.json').read_text(encoding='utf-8')) if c.get('id')}
    counts = collections.Counter()
    scopes = {'zh': [], 'en': []}
    manifest = []
    dedup_files = set()
    raw_names, raw_urls = set(), set()

    def consume(name, text):
        counts['downloaded_files'] += 1
        counts['raw_entries'] += text.count('#EXTINF:')
        for line in text.splitlines():
            if line.startswith('#EXTINF:'):
                raw_names.add(line.rsplit(',', 1)[-1].strip())
            elif line.strip() and not line.startswith('#') and '://' in line:
                raw_urls.add(line.strip())
        try:
            entries = parse(text)
        except ValueError:
            manifest.append({'file': name, 'state': 'no_supported_entries', 'entries': 0})
            return
        languages = collections.Counter()
        for entry in entries:
            language = classify(entry, database)
            languages[language] += 1
            counts[language + '_entries'] += 1
            if language in scopes:
                scopes[language].append(entry)
        manifest.append({'file': name, 'state': 'parsed', 'entries': len(entries), 'languages': dict(languages)})

    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode='r:gz') as archive:
        total = 0
        for member in sorted(archive.getmembers(), key=lambda m: m.name):
            if not member.isfile() or not re.search(r'\.(m3u8?|txt)$', member.name, re.I):
                continue
            if member.size > 16*1024*1024:
                raise ValueError('Oversized catalog member')
            total += member.size
            if total > 128*1024*1024:
                raise ValueError('Oversized expanded catalog')
            consume(member.name.split('/', 1)[-1], decode(archive.extractfile(member).read()))
            counts['repository_files'] += 1
    # Public catalogues are configurable; cached copies remain attributable.
    urls = catalog.get('playlist_urls', ['https://iptv-org.github.io/iptv/index.m3u'])
    for index, url in enumerate(urls):
        cached = cache / ('public-playlist-{}.m3u'.format(hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]))
        try:
            external = fetch(url)
            cached.write_bytes(external)
            consume('external/public-playlist-{}.m3u'.format(index), decode(external))
        except RuntimeError:
            counts['external_download_failed'] += 1
            if cached.exists():
                consume('external/public-playlist-{}.cached.m3u'.format(index), decode(cached.read_bytes()))
    if progress:
        progress('catalog_classify')
    for language, entries in scopes.items():
        atomic_text(root / (language + '.m3u'), playlist(entries))
        counts[language + '_unique_channels'] = len({e['key'] for e in entries})
        counts[language + '_unique_addresses'] = len({e['endpoint']['id'] for e in entries})
    counts['raw_unique_names'] = len(raw_names)
    counts['raw_unique_addresses'] = len(raw_urls)
    baseline = set()
    existing = data / 'published/iptv_top5.m3u'
    if existing.exists():
        baseline = {e['key'] for e in parse(existing.read_text(encoding='utf-8'))}
    chinese = {e['key'] for e in scopes['zh']}
    counts['zh_overlap_current_available'] = len(chinese & baseline)
    counts['zh_additional_candidate_channels'] = len(chinese - baseline)
    report = {'repository': catalog.get('repository_name', 'imDazui/Tvlist-awesome-m3u-m3u8'), 'stale_archive': stale,
              'counts': dict(counts), 'files': manifest,
              'language_policy': 'Chinese to main, English to separate list; other or unknown languages and NSFW excluded'}
    write_json(root / 'inventory.json', report)
    return root, database, report
