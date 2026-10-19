"""Loopback-only HA transport for browser microphone use; no API key or inference.

Run on the computer holding the microphone. HA still authenticates each browser.
Never bind to a LAN interface, inject an access token or relax browser security.
"""
import argparse
import asyncio
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
from multidict import CIMultiDict
from yarl import URL

HOP={'connection','keep-alive','proxy-authenticate','proxy-authorization','te','trailer','transfer-encoding','upgrade'}
UPSTREAM=web.AppKey('upstream',aiohttp.ClientSession)


def clean_headers(headers,websocket=False):
    result=CIMultiDict()
    blocked=HOP|{v.strip().lower() for v in headers.get('Connection','').split(',')}
    for key,value in headers.items():
        low=key.lower()
        if low in blocked or low.startswith('x-forwarded-') or low=='forwarded':continue
        if websocket and low.startswith('sec-websocket-'):continue
        result.add(key,value)
    return result


def allowed_request(host,origin,port):
    hosts={f'localhost:{port}',f'127.0.0.1:{port}'}
    return host.lower() in hosts and (not origin or origin in {f'http://{h}' for h in hosts})


def create_app(target,port):
    parsed=urlsplit(target)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
        raise ValueError('Provide the plain HA base URL, without credentials')
    target=target.rstrip('/')
    app=web.Application(client_max_size=32*1024**2)

    async def session_context(app):
        async with aiohttp.ClientSession(auto_decompress=False,trust_env=False,timeout=aiohttp.ClientTimeout(total=None,sock_connect=15)) as session:
            app[UPSTREAM]=session;yield
    app.cleanup_ctx.append(session_context)

    async def proxy(request):
        if not allowed_request(request.host,request.headers.get('Origin'),port):raise web.HTTPForbidden(text='Local microphone gateway only')
        url=URL(target+request.raw_path,encoded=True)
        if request.headers.get('Upgrade','').lower()=='websocket':
            protocols=tuple(p.strip() for p in request.headers.get('Sec-WebSocket-Protocol','').split(',') if p.strip())
            async with request.app[UPSTREAM].ws_connect(url,headers=clean_headers(request.headers,True),protocols=protocols,max_msg_size=32*1024**2) as remote:
                local=web.WebSocketResponse(protocols=protocols,max_msg_size=32*1024**2)
                await local.prepare(request)
                async def relay(source,destination):
                    async for message in source:
                        if message.type==aiohttp.WSMsgType.TEXT:await destination.send_str(message.data)
                        elif message.type==aiohttp.WSMsgType.BINARY:await destination.send_bytes(message.data)
                        elif message.type==aiohttp.WSMsgType.ERROR:break
                tasks=[asyncio.create_task(relay(local,remote)),asyncio.create_task(relay(remote,local))]
                try:await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:task.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True)
                    await local.close();await remote.close()
                return local
        body=request.content.iter_chunked(65536) if request.can_read_body else None
        async with request.app[UPSTREAM].request(request.method,url,headers=clean_headers(request.headers),data=body,allow_redirects=False) as remote:
            headers=clean_headers(remote.headers)
            if headers.get('Location','').startswith(target+'/'):
                headers['Location']=str(request.url.origin())+headers['Location'][len(target):]
            response=web.StreamResponse(status=remote.status,headers=headers)
            await response.prepare(request)
            async for chunk in remote.content.iter_chunked(65536):await response.write(chunk)
            await response.write_eof();return response
    app.router.add_route('*','/{path:.*}',proxy)
    return app


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--ha-url',required=True);parser.add_argument('--port',type=int,default=18123)
    args=parser.parse_args()
    if not 1024<=args.port<=65535:parser.error('Choose a port from 1024 to 65535')
    web.run_app(create_app(args.ha_url,args.port),host='127.0.0.1',port=args.port,access_log=None,print=None)


if __name__=='__main__':main()
