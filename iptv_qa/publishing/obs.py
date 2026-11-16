"""Optional object-storage adapter; no detector or player imports."""
import json
import os
from pathlib import Path
import re
import time
from iptv_qa.common.io import write_json


def obs_settings(settings):
    """Resolve region defaults without importing an SDK or reading credentials."""
    obs = dict(settings or {})
    if obs.get('enabled') and not obs.get('endpoint'):
        region = obs.get('region', '')
        if not re.match(r'^[a-z0-9]+(?:-[a-z0-9]+)+$', region):
            raise ValueError('Set obs.endpoint or a valid obs.region')
        obs['endpoint'] = 'https://obs.{}.myhuaweicloud.com'.format(region)
    return obs

def publish_obs(export_dir, obs, filenames=None):
    """Optional Huawei OBS adapter; disabled until endpoint/bucket/keys are supplied."""
    if not obs.get('enabled'):
        return {'enabled': False, 'state': 'not_configured'}
    obs = obs_settings(obs)
    if not obs.get('endpoint', '').startswith('https://') or not obs.get('bucket'):
        raise RuntimeError('OBS requires an HTTPS endpoint and bucket')
    from obs import ObsClient, PutObjectHeader
    from certifi import where as trust_bundle
    ak, sk = os.environ.get('OBS_ACCESS_KEY_ID'), os.environ.get('OBS_SECRET_ACCESS_KEY')
    if not ak or not sk:
        raise RuntimeError('OBS credentials are missing from the service environment')
    client = ObsClient(access_key_id=ak, secret_access_key=sk,
                       security_token=os.environ.get('OBS_SECURITY_TOKEN'), server=obs['endpoint'],
                       ssl_verify=obs.get('ca_bundle') or trust_bundle())
    prefix = obs.get('prefix', 'iptv/').strip('/')
    uploaded = []
    if filenames is None:
        filenames = (tuple(obs['playlist_files']) if obs.get('playlist_files') else ('iptv_top5.m3u', 'iptv_top3.m3u')
                     + (('iptv_set1.m3u', 'iptv_set2.m3u', 'iptv_set3.m3u') if obs.get('publish_sets') else ())
                     + (('report.json',) if obs.get('upload_report', True) else ()))
    names = obs.get('object_names', {})
    if any(not re.match(r'^[A-Za-z0-9_.-]+$', names.get(filename, filename)) for filename in filenames):
        raise ValueError('OBS object names must be simple filenames; configure directories using prefix')
    object_keys = ['/'.join(part for part in (prefix, names.get(filename, filename)) if part) for filename in filenames]
    try:
        # Fixed object keys keep the subscription URLs stable. Never change bucket ACLs.
        for filename in filenames:
            key = '/'.join(part for part in (prefix, names.get(filename, filename)) if part)
            if filename == 'report.json':
                report = json.loads((export_dir / filename).read_text(encoding='utf-8'))
                report['obs'] = {'enabled': True, 'state': 'uploaded', 'objects': object_keys}
                write_json(export_dir / filename, report)
            headers = PutObjectHeader()
            headers.contentType = ({'.json': 'application/json; charset=utf-8', '.html': 'text/html; charset=utf-8',
                                    '.csv': 'text/csv; charset=utf-8'}.get(Path(filename).suffix, 'audio/x-mpegurl; charset=utf-8'))
            last_error = 'upload_failed'
            retries = int(obs.get('upload_retries', 3))
            if not 1 <= retries <= 10:
                raise ValueError('OBS upload_retries must be between 1 and 10')
            for attempt in range(retries):
                try:
                    response = client.putFile(obs['bucket'], key, str(export_dir / filename), headers=headers,
                                              extensionHeaders={'Cache-Control': obs.get('cache_control', 'max-age=300')})
                    if 200 <= response.status < 300:
                        uploaded.append(key)
                        break
                    last_error = str(response.status)
                except Exception as exc:
                    last_error = type(exc).__name__
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
            else:
                raise RuntimeError('OBS upload failed for {} ({})'.format(filename, last_error))
    finally:
        client.close()
    return {'enabled': True, 'state': 'uploaded', 'objects': uploaded}
