import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import urllib.request
from assets import ASSETS, MODELS,HEAD_FILES
from urllib.parse import urlsplit


class Downloads:
    def __init__(self, directory, seed='/share/router_runtime_seed'):
        self.root=Path(directory);self.root.mkdir(parents=True,exist_ok=True)
        self.seed=Path(seed);self.lock=threading.RLock();self.jobs={};self.verified=set()
        self.source_file=self.root/'model_sources.json'
        try:self.sources=json.loads(self.source_file.read_text('utf-8'))
        except (OSError,ValueError):self.sources={}

    def valid(self,key):
        filename,_,digest,size=ASSETS[key];path=self.root/filename
        if not path.is_file() or path.stat().st_size!=size:return False
        stamp=(key,path.stat().st_mtime_ns)
        if stamp in self.verified:return True
        with path.open('rb') as f:good=hashlib.file_digest(f,'sha256').hexdigest()==digest
        if good:self.verified.add(stamp)
        return good

    def ready(self,name):
        if not all(self.valid(k) for k in MODELS[name]['assets']):return False
        if name=='router':
            for filename,digest in HEAD_FILES.items():
                path=self.root/filename
                if not path.is_file():return False
                stamp=(filename,path.stat().st_mtime_ns)
                if stamp not in self.verified:
                    with path.open('rb') as f:good=hashlib.file_digest(f,'sha256').hexdigest()==digest
                    if not good:return False
                    self.verified.add(stamp)
        return True

    def set_source(self,url):
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.hostname!='github.com' or parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment or '/releases/download/' not in parsed.path or not parsed.path.endswith('/router-heads-v4.zip'):
            raise ValueError('invalid_model_source')
        with self.lock:
            if any(j.get('state')=='downloading' for j in self.jobs.values()):raise ValueError('download_busy')
            self.sources={'heads':url};partial=self.source_file.with_suffix('.tmp')
            partial.write_text(json.dumps(self.sources),encoding='utf-8');os.replace(partial,self.source_file)

    def install_heads(self):
        import zipfile,tempfile
        with zipfile.ZipFile(self.root/ASSETS['heads'][0]) as z:
            if set(z.namelist())!=set(HEAD_FILES) or len(z.namelist())!=len(HEAD_FILES):raise ValueError('invalid_head_bundle')
            with tempfile.TemporaryDirectory(dir=self.root) as temp:
                for name,digest in HEAD_FILES.items():
                    info=z.getinfo(name)
                    if info.file_size>2*1024**2:raise ValueError('invalid_head_bundle')
                    data=z.read(name)
                    if hashlib.sha256(data).hexdigest()!=digest:raise ValueError('checksum_mismatch')
                    path=Path(temp)/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
                for name in HEAD_FILES:
                    target=self.root/name;target.parent.mkdir(parents=True,exist_ok=True);os.replace(Path(temp)/name,target)

    def delete(self,name):
        if name not in ('kokoro_fp32','kokoro_fp16'):raise ValueError('model_cannot_delete')
        with self.lock:
            if any(j.get('state')=='downloading' for j in self.jobs.values()):raise ValueError('download_busy')
            shared=set()
            for other,meta in MODELS.items():
                if other!=name and self.ready(other):shared.update(meta['assets'])
            removed=0
            for key in MODELS[name]['assets']:
                if key in shared:continue
                path=self.root/ASSETS[key][0]
                if path.is_file():removed+=path.stat().st_size;path.unlink()
            self.jobs.pop(name,None)
            return {'deleted':name,'freed_bytes':removed}

    def catalog(self):
        with self.lock:
            return [dict(id=name,**meta,bytes=sum(ASSETS[k][3] for k in meta['assets']),
                         installed=self.ready(name),job=dict(self.jobs.get(name,{}))) for name,meta in MODELS.items()]

    def start(self,name):
        if name not in MODELS:raise ValueError('unknown_model')
        with self.lock:
            if self.jobs.get(name,{}).get('state')=='downloading':return
            # One writer at a time: shared voice/config files must not race.
            if any(j.get('state')=='downloading' for j in self.jobs.values()):raise ValueError('download_busy')
            self.jobs[name]={'state':'downloading','received':0,'total':sum(ASSETS[k][3] for k in MODELS[name]['assets'])}
        threading.Thread(target=self._run,args=(name,),daemon=True).start()

    def _run(self,name):
        done=0
        try:
            for key in MODELS[name]['assets']:
                filename,url,digest,size=ASSETS[key];target=self.root/filename
                target.parent.mkdir(parents=True,exist_ok=True)
                if not self.valid(key):
                    partial=target.with_suffix(target.suffix+'.partial')
                    try:
                        seed=self.seed/filename
                        url=self.sources.get(key,url)
                        if not seed.is_file() and not url:raise ValueError('model_source_required')
                        source=seed.open('rb') if seed.is_file() else urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'router-runtime/0.8'}),timeout=45)
                        count=0;hasher=hashlib.sha256()
                        with source,partial.open('wb') as out:
                            while block:=source.read(1024*512):
                                count+=len(block)
                                if count>size:raise ValueError('size_mismatch')
                                out.write(block);hasher.update(block)
                                with self.lock:self.jobs[name]['received']=done+count
                        if count!=size or hasher.hexdigest()!=digest:raise ValueError('checksum_mismatch')
                        os.replace(partial,target)
                    finally:
                        partial.unlink(missing_ok=True)
                done+=size
                with self.lock:self.jobs[name]['received']=done
            if name=='router':self.install_heads()
            with self.lock:self.jobs[name].update(state='ready',received=done)
        except Exception as e:
            # Never expose URLs or transport credential content in API errors.
            code=str(e) if str(e) in ('size_mismatch','checksum_mismatch','model_source_required','invalid_head_bundle') else 'download_failed'
            with self.lock:self.jobs[name].update(state='error',error=code)
