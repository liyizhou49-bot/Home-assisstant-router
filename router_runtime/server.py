"""Internal-only HTTP compute service. No HA credentials or device execution."""
import os
for key in ('OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','OMP_NUM_THREADS'):os.environ.setdefault(key,'1')
import io
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import threading
import time
import wave
from assets import ASSETS, MODELS, VOICES
from downloads import Downloads

ROOT=Path(__file__).parent
DATA=Path(os.environ.get('ROUTER_DATA','/data/models'))


class Engine:
    def __init__(self):
        self.downloads=Downloads(DATA);self.route_lock=threading.Lock();self.tts_lock=threading.Lock()
        self.models={};self.corrector=None;self.speech=None;self.speech_key=None

    def load_router(self):
        if self.models:return
        if not self.downloads.ready('router'):raise ValueError('router_not_ready')
        from ha_router import Router
        from ha_router.semantic import ChineseEncoder
        from ha_router.hybrid import HybridClassifier
        from ha_router.correction import LocalCorrector
        # Model metadata references a sibling encoder; custom encoder loads persistent data.
        encoder=ChineseEncoder(DATA/'encoder_bge_zh')
        head_dir=DATA/'hybrid_v4'
        self.models={'binary_v1':Router(DATA/'binary_v1'),
                     'hybrid_v4':Router(classifier=HybridClassifier.load(head_dir,encoder=encoder))}
        self.corrector=LocalCorrector(ROOT/'lab_commands.json')

    def route(self,text,model):
        with self.route_lock:
            self.load_router()
            if model not in self.models:raise ValueError('unknown_model')
            encoder=getattr(self.models[model].classifier,'encoder',None)
            if encoder and len(encoder.cache)>128:encoder.cache.clear()
            started=time.perf_counter();result=self.models[model].route(text)
            return dict(result,model=model,elapsed_ms=round((time.perf_counter()-started)*1000,3),compute='ha_vm')

    def synthesize(self,payload):
        model=payload.get('model','kokoro_fp32');voice=payload.get('voice','zf_001');threads=payload.get('threads',2)
        if model not in ('kokoro_fp32','kokoro_fp16') or voice not in VOICES or type(threads)!=int or threads not in (1,2,3,4):raise ValueError('invalid_tts_options')
        if not self.downloads.ready(model):raise ValueError('model_not_ready')
        with self.tts_lock:
            import numpy as np
            if self.speech_key!=(model,threads):
                self.speech=None
                import gc;gc.collect()
                import onnxruntime as ort
                from kokoro_onnx import Kokoro
                from misaki import zh
                options=ort.SessionOptions();options.intra_op_num_threads=threads;options.inter_op_num_threads=1
                session=ort.InferenceSession(str(DATA/ASSETS['fp32' if model=='kokoro_fp32' else 'fp16'][0]),sess_options=options,providers=['CPUExecutionProvider'])
                self.speech=(Kokoro.from_session(session,str(DATA/ASSETS['voices'][0]),vocab_config=str(DATA/ASSETS['config'][0])),zh.ZHG2P(version='1.1'))
                self.speech_key=(model,threads)
            engine,g2p=self.speech;phonemes,_=g2p(payload['text'])
            audio,rate=engine.create(phonemes,voice=voice,speed=1.0,is_phonemes=True)
            if not len(audio) or not np.isfinite(audio).all():raise ValueError('invalid_audio')
            result=io.BytesIO()
            with wave.open(result,'wb') as w:
                w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate)
                w.writeframes((np.clip(audio,-1,1)*32767).astype('<i2').tobytes())
            return result.getvalue()


class Handler(BaseHTTPRequestHandler):
    engine=None
    def log_message(self,*args):pass
    def reply(self,status,data,kind='application/json'):
        if isinstance(data,dict) or isinstance(data,list):data=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
    def do_GET(self):
        if self.path=='/health':self.reply(200,dict(service='router_runtime',version='0.8.0',compute='ha_vm',router_ready=self.engine.downloads.ready('router'),training=False,device_execution=False,cpu_threads=2))
        elif self.path=='/models':self.reply(200,dict(models=self.engine.downloads.catalog(),voices=VOICES,router_model_url=self.engine.downloads.sources.get('heads','')))
        else:self.reply(404,{'error':'not_found'})
    def do_POST(self):
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=16384:raise ValueError('request_size')
            self.connection.settimeout(30);payload=json.loads(self.rfile.read(size))
            if not isinstance(payload,dict):raise ValueError('invalid_request')
            if self.path=='/models/download':
                self.engine.downloads.start(payload.get('model'));self.reply(200,{'accepted':True});return
            if self.path=='/models/source':
                self.engine.downloads.set_source(payload.get('url',''));self.reply(200,{'saved':True});return
            if self.path=='/models/delete':
                if not self.engine.tts_lock.acquire(blocking=False):raise ValueError('tts_busy')
                try:
                    result=self.engine.downloads.delete(payload.get('model'))
                    if self.engine.speech_key and self.engine.speech_key[0]==payload.get('model'):
                        self.engine.speech=None;self.engine.speech_key=None
                        import gc;gc.collect()
                finally:self.engine.tts_lock.release()
                self.reply(200,result);return
            text=payload.get('text')
            if not isinstance(text,str) or not text.strip() or len(text)>(1500 if self.path=='/tts' else 500):raise ValueError('invalid_text')
            if self.path=='/route':self.reply(200,self.engine.route(text,payload.get('model','hybrid_v4')))
            elif self.path=='/correct':
                with self.engine.route_lock:
                    self.engine.load_router();result=self.engine.corrector.correct(text)
                self.reply(200,result)
            elif self.path=='/tts':self.reply(200,self.engine.synthesize(payload),'audio/wav')
            else:self.reply(404,{'error':'not_found'})
        except (ValueError,TypeError,KeyError) as error:
            code=str(error)
            self.reply(400,{'error':code if code in ('download_busy','tts_busy','invalid_model_source','model_cannot_delete','unknown_model') else 'invalid_request_or_model_not_ready'})
        except Exception:
            import logging
            logging.exception('Internal compute failed')
            self.reply(500,{'error':'compute_failed'})


if __name__=='__main__':
    Handler.engine=Engine()
    Handler.engine.downloads.start('router')
    http=ThreadingHTTPServer(('0.0.0.0',17861),Handler);http.daemon_threads=True
    print('Internal compute ready. No host port exposed.',flush=True)
    http.serve_forever()
