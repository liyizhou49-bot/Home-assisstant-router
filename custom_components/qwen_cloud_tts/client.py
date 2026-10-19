"""Async duplex TTS protocol, with no SDK and no secret-bearing error messages."""
import asyncio
import base64
from contextlib import suppress
import json
import re
from time import monotonic
from urllib.parse import urlsplit
import uuid

import aiohttp
from .const import MODELS


class CloudTTSError(Exception):
    def __init__(self, code):
        self.code = re.sub(r'[^A-Za-z0-9_.-]', '_', str(code))[:60]
        super().__init__(self.code)


def validate_host(host):
    host = host.strip().lower()
    parsed = urlsplit(host if '://' in host else 'https://' + host)
    name=parsed.hostname or ''
    if (parsed.scheme!='https' or parsed.username or parsed.password or parsed.port is not None or parsed.path not in ('','/') or parsed.query or parsed.fragment
            or not (name in ('dashscope.aliyuncs.com','dashscope-intl.aliyuncs.com') or re.fullmatch(r'[a-z0-9-]+\.(cn-beijing|ap-southeast-1)\.maas\.aliyuncs\.com',name))):
        raise ValueError('invalid_host')
    return name


class QwenClient:
    def __init__(self, session, host, api_key, model='qwen3-tts-flash-realtime'):
        self.session = session
        self.host = validate_host(host)
        self.api_key = api_key
        if model not in MODELS:raise ValueError('invalid_model')
        self.model = model

    @property
    def protocol(self):return MODELS[self.model]['protocol']

    async def _connect(self):
        try:
            async with asyncio.timeout(6):
                return await self.session.ws_connect(
                    f'wss://{self.host}/api-ws/v1/realtime?model={self.model}',
                    headers={'Authorization': 'Bearer ' + self.api_key}, heartbeat=20,
                    max_msg_size=8 * 1024 * 1024)
        except asyncio.CancelledError:
            raise
        except aiohttp.WSServerHandshakeError as error:
            raise CloudTTSError('invalid_auth' if error.status in (401, 403) else 'connect_failed') from None
        except Exception:
            raise CloudTTSError('connect_failed') from None

    async def _receive(self, sock, timeout=8):
        try:
            message = await sock.receive(timeout=timeout)
            if message.type != aiohttp.WSMsgType.TEXT:
                raise CloudTTSError('connection_closed')
            event = json.loads(message.data)
            if event.get('type') == 'error':
                raise CloudTTSError(event.get('error', {}).get('code', 'service_error'))
            return event
        except (asyncio.CancelledError, CloudTTSError):
            raise
        except TimeoutError:
            raise CloudTTSError('audio_timeout') from None
        except Exception:
            raise CloudTTSError('invalid_response') from None

    async def _send(self, sock, kind, **fields):
        await sock.send_json({'event_id': 'event_' + uuid.uuid4().hex, 'type': kind, **fields})

    async def _prepare(self, sock, voice):
        if (await self._receive(sock)).get('type') != 'session.created':
            raise CloudTTSError('invalid_handshake')
        await self._send(sock, 'session.update', session={
            'voice': voice, 'mode': 'server_commit', 'language_type': 'Chinese',
            'response_format': 'mp3', 'sample_rate': 24000})
        if (await self._receive(sock)).get('type') != 'session.updated':
            raise CloudTTSError('invalid_session')

    async def validate(self, voice):
        if voice not in MODELS[self.model]['voices']:raise ValueError('invalid_voice')
        if self.protocol!='realtime':
            if self.protocol=='qwen_http':await self.file('你好。',voice,{})
            else:
                from contextlib import aclosing
                async def text():yield '你好。'
                async with aclosing(self.stream(text(),voice,{})) as stream:
                    async for _ in stream:break
            return
        sock = await self._connect()
        try:
            await self._prepare(sock, voice)
        finally:
            await sock.close()

    async def stream(self, text_gen, voice, metrics):
        if self.protocol=='audio_http':
            async for chunk in self.http_stream(text_gen,voice,metrics):yield chunk
            return
        started = monotonic()
        sock = await self._connect()
        sender = None
        audio_started = False
        metrics.update(status='connecting', characters=0, chunks=0)
        try:
            await self._prepare(sock, voice)
            metrics['status'] = 'streaming'

            async def send_text():
                try:
                    async for chunk in text_gen:
                        if chunk:
                            await self._send(sock, 'input_text_buffer.append', text=chunk)
                    await self._send(sock, 'session.finish')
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await sock.close()
                    raise CloudTTSError('text_input_failed') from None

            sender = asyncio.create_task(send_text())
            responses = 0
            async with asyncio.timeout(120):
                while True:
                    event = await self._receive(sock, 20 if audio_started else 8)
                    kind = event.get('type')
                    if kind == 'response.audio.delta':
                        try:
                            data = base64.b64decode(event['delta'], validate=True)
                        except Exception:
                            raise CloudTTSError('invalid_audio') from None
                        if data:
                            if not audio_started:
                                audio_started = True
                                metrics['first_audio_ms'] = round((monotonic() - started) * 1000, 2)
                            metrics['chunks'] += 1
                            yield data
                    elif kind == 'response.done':
                        if event.get('response', {}).get('status') == 'failed':
                            raise CloudTTSError('response_failed')
                        responses += 1
                        value = event.get('response', {}).get('usage', {}).get('characters', 0)
                        if isinstance(value, int):
                            metrics['characters'] += value
                    elif kind == 'session.finished':
                        break
            await sender
            if not audio_started or not responses:
                raise CloudTTSError('empty_audio')
            metrics.update(status='complete', total_ms=round((monotonic() - started) * 1000, 2))
        except asyncio.CancelledError:
            raise
        except CloudTTSError:
            raise
        except Exception:
            raise CloudTTSError('stream_failed') from None
        finally:
            if sender:
                if not sender.done():
                    sender.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await sender
            await sock.close()

    async def file(self,text,voice,metrics):
        """Qwen3 HTTP emits PCM chunks; fetch its complete WAV without forwarding auth."""
        started=monotonic()
        try:
            async with self.session.post('https://'+self.host+'/api/v1/services/aigc/multimodal-generation/generation',
                headers={'Authorization':'Bearer '+self.api_key},json={'model':self.model,'input':{'text':text,'voice':voice,'language_type':'Chinese'}},timeout=aiohttp.ClientTimeout(total=90)) as response:
                if response.status!=200:raise CloudTTSError('invalid_auth' if response.status in (401,403) else 'synthesis_failed')
                body=await response.json();url=body.get('output',{}).get('audio',{}).get('url','')
            parsed=urlsplit(url)
            if parsed.scheme not in ('http','https') or parsed.username or parsed.password or parsed.port or not (parsed.hostname or '').endswith('.aliyuncs.com'):
                raise CloudTTSError('invalid_audio_url')
            async with self.session.get(url,allow_redirects=False,timeout=aiohttp.ClientTimeout(total=45)) as audio:
                if audio.status!=200:raise CloudTTSError('audio_download_failed')
                parts=[];total=0
                async for block in audio.content.iter_chunked(32768):
                    total+=len(block)
                    if total>20*1024**2:raise CloudTTSError('audio_too_large')
                    parts.append(block)
            result=b''.join(parts)
            if not result.startswith(b'RIFF'):raise CloudTTSError('invalid_audio')
            metrics.update(status='complete',model=self.model,characters=body.get('usage',{}).get('characters',len(text)),total_ms=round((monotonic()-started)*1000,2))
            return result
        except (CloudTTSError,asyncio.CancelledError):raise
        except Exception:raise CloudTTSError('synthesis_failed') from None

    async def http_stream(self,text_gen,voice,metrics):
        started=monotonic();text=''
        async for part in text_gen:
            text+=part
            if len(text)>10000:raise CloudTTSError('text_too_long')
        metrics.update(status='connecting',model=self.model,characters=len(text),chunks=0)
        if not text.strip():raise CloudTTSError('empty_text')
        try:
            async with self.session.post('https://'+self.host+'/api/v1/services/audio/tts/SpeechSynthesizer',
                headers={'Authorization':'Bearer '+self.api_key,'X-DashScope-SSE':'enable'},
                json={'model':self.model,'input':{'text':text,'voice':voice,'format':'mp3','sample_rate':24000}},
                timeout=aiohttp.ClientTimeout(total=120,sock_read=25)) as response:
                if response.status!=200:raise CloudTTSError('invalid_auth' if response.status in (401,403) else 'synthesis_failed')
                async for event in sse_events(response.content):
                    if event.get('code'):raise CloudTTSError('synthesis_failed')
                    output=event.get('output',{});encoded=output.get('audio',{}).get('data')
                    if encoded:
                        data=base64.b64decode(encoded,validate=True)
                        if data:
                            if not metrics['chunks']:metrics['first_audio_ms']=round((monotonic()-started)*1000,2)
                            metrics['chunks']+=1;yield data
                    usage=event.get('usage',{}).get('characters')
                    if isinstance(usage,int):metrics['characters']=usage
            if not metrics['chunks']:raise CloudTTSError('empty_audio')
            metrics.update(status='complete',total_ms=round((monotonic()-started)*1000,2))
        except (CloudTTSError,asyncio.CancelledError):raise
        except Exception:raise CloudTTSError('synthesis_failed') from None


async def sse_events(content):
    buffer=b'';total=0
    async for block in content.iter_chunked(16384):
        buffer+=block;total+=len(block)
        if len(buffer)>4*1024**2 or total>32*1024**2:raise CloudTTSError('response_too_large')
        buffer=buffer.replace(b'\r\n',b'\n')
        while b'\n\n' in buffer:
            frame,buffer=buffer.split(b'\n\n',1)
            data=b'\n'.join(line[5:].lstrip() for line in frame.split(b'\n') if line.startswith(b'data:'))
            if data and data!=b'[DONE]':
                value=json.loads(data)
                if not isinstance(value,dict):raise CloudTTSError('invalid_response')
                yield value


class ReplayText:
    """Keep input alive across a pre-audio cloud failure; replay it for local TTS."""
    def __init__(self, source):
        self.chunks = []
        self.done = False
        self.error = None
        self.condition = asyncio.Condition()
        self.task = asyncio.create_task(self._collect(source))

    async def _collect(self, source):
        try:
            total = 0
            async for chunk in source:
                if not chunk:
                    continue
                total += len(chunk)
                if total > 10000:
                    raise CloudTTSError('text_too_long')
                async with self.condition:
                    self.chunks.append(chunk)
                    self.condition.notify_all()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.error = CloudTTSError('text_input_failed')
        finally:
            async with self.condition:
                self.done = True
                self.condition.notify_all()

    async def stream(self):
        index = 0
        while True:
            async with self.condition:
                await self.condition.wait_for(lambda: index < len(self.chunks) or self.done)
                if index >= len(self.chunks):
                    if self.error:
                        raise self.error
                    return
                chunk = self.chunks[index]
                index += 1
            yield chunk

    async def complete(self):
        await self.task
        if self.error:
            raise self.error
        return ''.join(self.chunks)

    async def close(self):
        if not self.task.done():
            self.task.cancel()
        with suppress(asyncio.CancelledError):
            await self.task
