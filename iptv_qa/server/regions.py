"""Optional, operator-configured regional dashboard aggregation."""
import html
import json
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request

from iptv_qa.reporting.history import render_html


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def load_regions(path):
    if not path:
        return None
    with open(path, encoding='utf-8') as f:
        config = json.load(f)
    if not config.get('enabled', False):
        return None
    nodes = config.get('nodes', [])
    if not isinstance(nodes, list) or not nodes:
        raise ValueError('Enabled multi-ECS dashboard requires nodes')
    result = []
    for node in nodes:
        url = node['url'].rstrip('/')
        parts = urlsplit(url)
        if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username
                or parts.password or parts.path or parts.query or parts.fragment):
            raise ValueError('Node URL must be an HTTP(S) origin')
        result.append({'name': str(node['name']), 'url': url,
                       'audit': bool(node.get('audit', False))})
    return {'local_name': str(config.get('local_name', '本机')), 'nodes': result}


def fetch_json(node, path):
    if path not in ('/api/status', '/api/history', '/api/guangzhou-audit'):
        raise ValueError('Unknown regional endpoint')
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(Request(node['url'] + path, headers={'Accept': 'application/json'}), timeout=10) as response:
        content = response.read(2 * 1024 * 1024 + 1)
    if len(content) > 2 * 1024 * 1024:
        raise ValueError('Regional response too large')
    return json.loads(content.decode('utf-8'))


def render_regions(config):
    sections = []
    for index, node in enumerate(config['nodes']):
        label = html.escape(node['name'])
        try:
            history = fetch_json(node, '/api/history')
            report = render_html(history['runs'], history['generated_at'], node['url'])
            report = report.split('<body><main>', 1)[1].split('</main></body>', 1)[0]
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            report = '<p>暂时无法读取该地区历史记录。</p>'
        sections.append('<section class="region"><h1>{}检测结果</h1><div class="card" id="region-status-{}">读取检测进度…</div>{}</section>'.format(label, index, report))
    script = '''<script>
async function refreshRegions(){const names={running:'进行中',complete:'已完成',failed:'失败',publication_blocked:'发布受保护机制阻止'};const detail=s=>(names[s.state]||s.state||'等待首次检测')+(s.state==='running'?' · '+(s.stage||'')+' · '+(s.completed||0)+' / '+(s.total||0)+' · 通过 '+(s.passed||0):'');for(let i=0;i<COUNT;i++){let el=document.getElementById('region-status-'+i);try{let r=await fetch('/api/regions/'+i,{cache:'no-store'});if(!r.ok)throw Error();let x=await r.json(),s=(x.status||{}).run||{},a=x.audit;let t='检测状态：'+detail(s);if(a)t+='；专项检测：'+detail(a);el.textContent=t;}catch(e){el.textContent='该地区暂时无法连接，请稍后重试。';}}}refreshRegions();setInterval(refreshRegions,30000);
</script>'''.replace('COUNT', str(len(sections)))
    return ''.join(sections), script
