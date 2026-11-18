"""Consistent, authenticated-encrypted backups; never upload plaintext/key files."""
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid
import zipfile
from urllib.error import HTTPError
from urllib.request import urlopen

from iptv_qa.common.io import utcnow
from iptv_qa.publishing.obs import obs_settings


def sanitized(value):
    if isinstance(value, dict):
        return {k: sanitized(v) for k,v in value.items()
                if not any(t in k.lower() for t in ('password','secret','access_key','security_token'))}
    if isinstance(value, list):
        return [sanitized(v) for v in value]
    return value


def restore_backup(path, key_path, destination):
    from cryptography.fernet import Fernet
    destination = Path(destination)
    if destination.exists():
        raise ValueError('restore_destination_exists')
    raw = Fernet(Path(key_path).read_bytes().strip()).decrypt(Path(path).read_bytes())
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        db_bytes = archive.read('catalog.sqlite3')
        manifest = json.loads(archive.read('manifest.json'))
        if hashlib.sha256(db_bytes).hexdigest() != manifest['database_sha256']:
            raise ValueError('backup_hash_mismatch')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb') as handle:
        handle.write(db_bytes)
    try:
        db=sqlite3.connect(str(destination))
        try:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('restore_integrity_failed')
            if db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] != '1':
                raise ValueError('unsupported_backup_schema')
        finally:
            db.close()
    except Exception:
        destination.unlink()
        raise
    if os.name != 'nt':
        destination.chmod(0o600)
    return manifest


def private_upload(path, cfg, key):
    from obs import ObsClient, PutObjectHeader
    from certifi import where
    settings = cfg['channel_library']['backup']
    obs = obs_settings(cfg.get('obs', {}))
    bucket = settings['bucket']
    client = ObsClient(access_key_id=os.environ['OBS_ACCESS_KEY_ID'], secret_access_key=os.environ['OBS_SECRET_ACCESS_KEY'],
        security_token=os.environ.get('OBS_SECURITY_TOKEN'), server=obs['endpoint'], ssl_verify=where())
    try:
        response = client.putFile(bucket, key, str(path), headers=PutObjectHeader(acl='private', contentType='application/octet-stream'))
        if response.status >= 300:
            raise RuntimeError('private_backup_upload_failed')
        acl = client.getObjectAcl(bucket, key)
        if acl.status >= 300 or any(getattr(g.grantee, 'group', None) for g in acl.body.grants or []):
            raise RuntimeError('backup_acl_not_private')
        # Bucket policies can override an object's private ACL: verify anonymous denial.
        region = obs['endpoint'].split('://',1)[1]
        anonymous = 'https://' + bucket + '.' + region + '/' + key
        try:
            with urlopen(anonymous, timeout=20) as response:
                response.read(1)
            raise RuntimeError('backup_anonymously_readable')
        except HTTPError as error:
            if error.code not in (403,404):
                raise
        with tempfile.TemporaryDirectory() as temp:
            fetched = Path(temp) / 'verified.enc'
            result = client.getObject(bucket, key, downloadPath=str(fetched))
            if result.status >= 300 or fetched.read_bytes() != Path(path).read_bytes():
                raise RuntimeError('backup_remote_hash_mismatch')
    finally:
        client.close()


def backup_library(library, cfg, uploader=None):
    from cryptography.fernet import Fernet
    settings = cfg['channel_library'].get('backup', {})
    if not settings.get('enabled'):
        raise ValueError('private_backup_not_configured')
    identity = uuid.uuid4().hex
    key = settings.get('prefix','library-private').strip('/') + '/' + utcnow().replace(':','') + '-' + identity + '.fernet'
    target = library.path.parent / 'backups' / (identity + '.fernet')
    target.parent.mkdir(exist_ok=True)
    state, error = 'failed', ''
    try:
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / 'catalog.sqlite3'
            library.snapshot_db(db_path)
            db_bytes = db_path.read_bytes()
            manifest = dict(created_at=utcnow(), database_sha256=hashlib.sha256(db_bytes).hexdigest(), schema_version=1)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('catalog.sqlite3', db_bytes)
                archive.writestr('config.redacted.json', json.dumps(sanitized(cfg)))
                archive.writestr('manifest.json', json.dumps(manifest))
            target.write_bytes(Fernet(Path(settings['key_path']).read_bytes().strip()).encrypt(buffer.getvalue()))
            if os.name != 'nt':
                target.chmod(0o600)
            # Every backup must restore successfully before it can be uploaded.
            restore_backup(target, settings['key_path'], Path(temp) / 'restore-test.sqlite3')
            (uploader or private_upload)(target, cfg, key)
            state = 'verified'
    except Exception as exc:
        error = type(exc).__name__
    digest = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else ''
    with library.connect() as db:
        db.execute('INSERT INTO backups VALUES (?,?,?,?,?,?)', (identity,state,key,digest,error,utcnow()))
    return dict(id=identity,state=state,error=error)
