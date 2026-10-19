import unittest
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from tools.microphone_gateway import create_app,allowed_request


class GatewayChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.seen=[]
        async def endpoint(request):
            if request.path=='/ws':
                socket=web.WebSocketResponse();await socket.prepare(request)
                async for message in socket:
                    if isinstance(message.data,bytes):await socket.send_bytes(message.data)
                    else:await socket.send_str(message.data)
                return socket
            self.seen.append(dict(request.headers))
            return web.Response(status=401,text='HA login still required')
        backend=web.Application();backend.router.add_route('*','/{path:.*}',endpoint)
        self.backend=TestServer(backend);await self.backend.start_server()
        self.client=TestClient(TestServer(create_app(str(self.backend.make_url('/')),18123)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close();await self.backend.close()

    async def test_no_authentication_bypass_or_forwarded_identity(self):
        result=await self.client.get('/api/',headers={'Host':'localhost:18123','X-Forwarded-For':'untrusted','Forwarded':'spoofed'})
        self.assertEqual(result.status,401)
        self.assertNotIn('Authorization',self.seen[-1]);self.assertNotIn('X-Forwarded-For',self.seen[-1]);self.assertNotIn('Forwarded',self.seen[-1])

    async def test_unknown_origin_and_host_rejected(self):
        for headers in [{'Host':'attacker.invalid'},{'Host':'localhost:18123','Origin':'https://attacker.invalid'}]:
            response=await self.client.get('/api/',headers=headers);self.assertEqual(response.status,403)
        self.assertEqual(self.seen,[])

    async def test_binary_audio_and_json_share_websocket(self):
        async with self.client.ws_connect('/ws',headers={'Host':'localhost:18123','Origin':'http://localhost:18123'}) as socket:
            await socket.send_str('{"type":"auth"}');self.assertEqual((await socket.receive()).data,'{"type":"auth"}')
            await socket.send_bytes(b'\x01\x00\x80\x00\x00');self.assertEqual((await socket.receive()).data,b'\x01\x00\x80\x00\x00')

    def test_endpoint_never_contains_credentials(self):
        for target in ['http://key:secret@example.invalid','http://example.invalid/?token=secret']:
            with self.assertRaises(ValueError):create_app(target,18123)

if __name__=='__main__':unittest.main()
