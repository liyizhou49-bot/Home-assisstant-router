"""Administrative connection used by setup/tests, never by the router service."""
import http.cookiejar
import json
import sys
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent



class HAClient:
    def __init__(self, base_url, token):
        self.base = base_url.rstrip('/')
        self.token = token.strip()
        if not self.token:
            raise ValueError('HA token is required')
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.cookies))

    def request(self, path, data=None, *, method=None, timeout=30, form=False, raw=False):
        body = None
        headers = {'Authorization': 'Bearer ' + self.token}
        if data is not None:
            if form:
                body = urllib.parse.urlencode(data).encode('utf-8')
                headers['Content-Type'] = 'application/x-www-form-urlencoded'
            else:
                body = json.dumps(data, ensure_ascii=False).encode('utf-8')
                headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.base + path, data=body, headers=headers,
                                         method=method or ('POST' if body is not None else 'GET'))
        try:
            with self.opener.open(request, timeout=timeout) as response:
                content = response.read().decode('utf-8')
        except urllib.error.HTTPError as error:
            # Do not include credentials, ingress URLs, or full server bodies in errors.
            raise RuntimeError(f'HA请求失败 HTTP {error.code}') from None
        if raw:
            return content
        return json.loads(content) if content else {}

    def supervisor(self, path, data=None, *, timeout=30):
        return self.websocket('supervisor/api', endpoint='/' + path.lstrip('/'),
                              method='post' if data is not None else 'get',
                              data=data or {}, timeout=timeout)

    def websocket(self, command, *, timeout=30, **fields):
        import websocket
        url = urllib.parse.urlsplit(self.base)
        address = urllib.parse.urlunsplit(
            ('wss' if url.scheme == 'https' else 'ws', url.netloc, '/api/websocket', '', ''))
        connection = websocket.create_connection(
            address, timeout=timeout + 5, http_no_proxy=[url.hostname], suppress_origin=True)
        try:
            if json.loads(connection.recv()).get('type') != 'auth_required':
                raise RuntimeError('HA WebSocket握手异常')
            connection.send(json.dumps({'type': 'auth', 'access_token': self.token}))
            if json.loads(connection.recv()).get('type') != 'auth_ok':
                raise RuntimeError('HA WebSocket认证失败')
            payload = dict(fields, id=1, type=command)
            if command == 'supervisor/api':
                payload['timeout'] = timeout
            connection.send(json.dumps(payload))
            while True:
                result = json.loads(connection.recv())
                if result.get('id') != 1:
                    continue
                if not result.get('success'):
                    code = result.get('error', {}).get('code', 'unknown_error')
                    raise RuntimeError(f'HA WebSocket操作失败: {code}')
                return result.get('result')
        finally:
            connection.close()

    def file_editor(self):
        info = self.supervisor('addons/core_configurator/info')
        session = self.supervisor('ingress/session', {})['session']
        host = urllib.parse.urlsplit(self.base).hostname
        self.cookies.set_cookie(http.cookiejar.Cookie(
            0, 'ingress_session', session, None, False, host, False, False,
            '/', True, False, None, True, None, None, {}))
        return info['ingress_url'].rstrip('/')

    def assist(self, text, pipeline_id, timeout=30):
        import websocket
        url = urllib.parse.urlsplit(self.base)
        address = urllib.parse.urlunsplit(
            ('wss' if url.scheme == 'https' else 'ws', url.netloc, '/api/websocket', '', ''))
        connection = websocket.create_connection(
            address, timeout=timeout, http_no_proxy=[url.hostname], suppress_origin=True)
        try:
            connection.recv()
            connection.send(json.dumps({'type': 'auth', 'access_token': self.token}))
            if json.loads(connection.recv()).get('type') != 'auth_ok':
                raise RuntimeError('HA认证失败')
            connection.send(json.dumps({'id':1, 'type':'assist_pipeline/run',
                'start_stage':'intent', 'end_stage':'intent', 'pipeline':pipeline_id,
                'input':{'text':text}}))
            events = []
            while True:
                message = json.loads(connection.recv())
                if message.get('type') == 'result' and not message.get('success'):
                    raise RuntimeError('Assist测试启动失败')
                if message.get('type') == 'event':
                    event = message['event']
                    events.append(event)
                    if event.get('type') in ('run-end','error'):
                        return events
        finally:
            connection.close()

