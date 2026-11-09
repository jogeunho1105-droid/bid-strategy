"""Private, versioned ledger persistence; no recommendation or presentation code."""
import base64
import hashlib
import http.client
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MAX_BYTES = 80 * 1024 * 1024


class StoreError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def write_atomic(destination, writer):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + destination.name + '-', dir=destination.parent)
    os.close(fd)
    try:
        writer(Path(name))
        os.replace(name, destination)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def config():
    try:
        import streamlit as st
        section = dict(st.secrets.get('ledger_store', {}))
    except (FileNotFoundError, KeyError):
        section = {}
    repository = section.get('repository', os.environ.get('LEDGER_STORE_REPOSITORY', ''))
    token = section.get('token', os.environ.get('LEDGER_STORE_TOKEN', ''))
    if not repository and not token:
        return None
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) or not token:
        raise StoreError('비공개 원장 저장소 설정을 확인하세요.')
    return {'repository': repository, 'token': token, 'branch': section.get('branch', 'main')}


class GitHubStore:
    def __init__(self, settings):
        self.settings = settings
        self.root = 'https://api.github.com/repos/' + settings['repository']
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, suffix='', body=None, raw=False, missing=False):
        headers = {'Authorization': 'Bearer ' + self.settings['token'],
                   'Accept': 'application/vnd.github.raw+json' if raw else 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2026-03-10', 'User-Agent': 'bid-strategy-ledger'}
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.root + suffix, data=data, headers=headers,
                                         method='PUT' if body is not None else 'GET')
        attempts = 3 if body is None else 1  # Retry reads; never repeat an uncertain write.
        for attempt in range(attempts):
            try:
                with self.opener.open(request, timeout=180) as response:
                    payload = response.read(MAX_BYTES + 1)
                if len(payload) > MAX_BYTES:
                    raise StoreError('비공개 원장 파일 크기 제한을 초과했습니다.')
                return payload if raw else json.loads(payload)
            except urllib.error.HTTPError as error:
                if missing and error.code == 404:
                    return None
                if attempt + 1 < attempts and error.code in (429, 500, 502, 503, 504):
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise StoreError('비공개 원장 저장소에 접근하지 못했습니다. 연결과 앱 전용 권한을 확인하세요.') from None
            except (OSError, http.client.HTTPException):
                if attempt + 1 < attempts:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise StoreError('비공개 원장 저장소 통신에 실패했습니다.') from None
            except ValueError:
                raise StoreError('비공개 원장 저장소 응답 검증에 실패했습니다.') from None

    def check_private(self):
        repo = self.request()
        if repo.get('private') is not True:
            raise StoreError('원장 저장소가 비공개가 아니므로 처리를 중단했습니다.')

    def path(self, name):
        return '/contents/' + urllib.parse.quote(name, safe='/')

    def read(self, name, raw=False, missing=False):
        return self.request(self.path(name) + '?ref=' + urllib.parse.quote(self.settings['branch'], safe=''),
                            raw=raw, missing=missing)

    def pointer(self, missing=False):
        obj = self.read('latest.json', missing=missing)
        if obj is None:
            return None, None
        try:
            pointer = json.loads(base64.b64decode(obj['content'], validate=False))
        except (KeyError, ValueError):
            raise StoreError('비공개 원장 버전 정보가 올바르지 않습니다.') from None
        digest = pointer.get('sha256', '')
        if (not re.fullmatch('[a-f0-9]{64}', digest)
                or pointer.get('path') != 'versions/' + digest + '.xlsx'
                or not isinstance(pointer.get('bytes'), int)
                or not 0 < pointer['bytes'] <= MAX_BYTES):
            raise StoreError('비공개 원장 버전 정보가 올바르지 않습니다.')
        return pointer, obj['sha']

    def download(self):
        import ledger_metadata
        self.check_private()
        pointer, _ = self.pointer()
        content = self.read(pointer['path'], raw=True)
        if len(content) != pointer['bytes'] or hashlib.sha256(content).hexdigest() != pointer['sha256']:
            raise StoreError('비공개 원장 파일의 무결성 검증에 실패했습니다.')
        try:
            frame = ledger_metadata.read_upload(content)
        except (ValueError, OSError, KeyError):
            raise StoreError('비공개 원장 내용 검증에 실패했습니다.') from None
        metadata = frame.attrs.get(ledger_metadata.ATTR, {})
        if metadata.get('version', 'upload-' + pointer['sha256'][:12]) != pointer.get('version'):
            raise StoreError('비공개 원장 데이터와 버전 정보가 일치하지 않습니다.')
        return frame

    def publish(self, content, frame):
        import ledger_metadata
        if not 0 < len(content) <= MAX_BYTES:
            raise StoreError('비공개 원장 파일 크기 제한을 초과했습니다.')
        self.check_private()
        previous, previous_sha = self.pointer(missing=True)
        digest = hashlib.sha256(content).hexdigest()
        pointer = {'format': 1, 'path': 'versions/' + digest + '.xlsx', 'sha256': digest,
                   'bytes': len(content), 'version': frame.attrs.get(ledger_metadata.ATTR, {}).get('version', 'upload-' + digest[:12])}
        if previous == pointer:
            # Repeated activation must also verify the immutable artifact's bytes.
            artifact = self.read(pointer['path'], raw=True)
            if hashlib.sha256(artifact).hexdigest() != digest:
                raise StoreError('비공개 원장 저장 검증에 실패했습니다.')
            return pointer
        artifact = self.read(pointer['path'], missing=True)
        if artifact is None:
            self.request(self.path(pointer['path']), {'message': 'Preserve immutable private ledger version',
                         'branch': self.settings['branch'], 'content': base64.b64encode(content).decode()})
        elif artifact.get('size') != len(content):
            raise StoreError('비공개 원장 원본을 확인하지 못했습니다.')
        # Verify bytes before promoting a version. Old artifacts are never overwritten.
        saved = self.read(pointer['path'], raw=True)
        if hashlib.sha256(saved).hexdigest() != digest:
            raise StoreError('비공개 원장 저장 검증에 실패했습니다.')
        body = {'message': 'Activate verified private ledger version', 'branch': self.settings['branch'],
                'content': base64.b64encode(json.dumps(pointer, ensure_ascii=False, indent=2).encode()).decode()}
        if previous_sha:
            body['sha'] = previous_sha
        self.request(self.path('latest.json'), body)
        return pointer


def persist_upload(content, frame, settings=None):
    settings = config() if settings is None else settings
    if settings:
        return GitHubStore(settings).publish(content, frame)
    return None


def restore_if_needed(history_file, save_history, settings=None):
    settings = config() if settings is None else settings
    if not settings:
        return False
    receipt = Path(str(history_file) + '.remote-source.json')
    if Path(history_file).exists() and receipt.exists():
        try:
            if json.loads(receipt.read_text(encoding='utf-8')).get('repository') == settings['repository']:
                return False
        except (OSError, ValueError):
            pass
    # Parse and validate the full operational Excel before touching the local state.
    # First connection also verifies the app's own credential, even with a local cache.
    frame = GitHubStore(settings).download()
    save_history(frame)
    import ledger_metadata
    confirmation = {'repository': settings['repository'],
                    'version': frame.attrs.get(ledger_metadata.ATTR, {}).get('version')}
    write_atomic(receipt, lambda p: p.write_text(json.dumps(confirmation), encoding='utf-8'))
    print(f'PRIVATE_LEDGER_RESTORE_OK operational_rows={len(frame)}', flush=True)
    return True
