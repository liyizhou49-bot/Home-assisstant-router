"""HAOS installer. Interactive token is kept in process memory, never persisted."""
import argparse
import getpass
import http.client
import json
from pathlib import Path
import tarfile
import tempfile
import time
from urllib.parse import urlencode,urlsplit
from ha_client import HAClient
from vm_internal_terminal import command,upload
from trace_card import upgrade as upgrade_trace_card

ROOT=Path(__file__).resolve().parent.parent


def wait_core(client,seconds=180):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:client.request('/api/config',timeout=5);return
        except Exception:time.sleep(3)
    raise RuntimeError('HA has not finished restarting; check its logs.')


def copy_integration(client,domain):
    ingress=client.file_editor();source=ROOT/'custom_components'/domain
    files=sorted(p for p in source.rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    folders={'/homeassistant/custom_components','/homeassistant/custom_components/'+domain}
    for file in files:
        relative=file.relative_to(source).parent.as_posix()
        if relative!='.':folders.add('/homeassistant/custom_components/'+domain+'/'+relative)
    for folder in sorted(folders,key=lambda v:len(v.split('/'))):
        parent,_,name=folder.rpartition('/')
        result=client.request(ingress+'/api/newfolder',{'path':parent,'name':name},form=True)
        if result.get('error') and 'exist' not in result.get('message','').lower():raise RuntimeError('Cannot create integration directory')
    for file in files:
        target='/homeassistant/custom_components/'+domain+'/'+file.relative_to(source).as_posix()
        text=file.read_text('utf-8')
        result=client.request(ingress+'/api/save',{'filename':target,'text':text},form=True)
        if result.get('error'):raise RuntimeError('Integration write failed')
        saved=client.request(ingress+'/api/file?'+urlencode({'filename':target}),raw=True)
        if text!=saved:raise RuntimeError('Integration readback failed')
    print('Verified integration: '+domain,flush=True)


def dashboard(client):
    dashboards=client.websocket('lovelace/dashboards/list')
    if not any(d['url_path']=='router-lab' for d in dashboards):
        client.websocket('lovelace/dashboards/create',url_path='router-lab',title='家庭助手',icon='mdi:home-assistant',show_in_sidebar=True,require_admin=False,mode='storage')
    try:config=client.websocket('lovelace/config',url_path='router-lab')
    except RuntimeError:config={'views':[]}
    wanted=[('settings','设置','mdi:cog','settings.html'),('voice','对话','mdi:account-voice','voice.html')]
    config['views']=[v for v in config.get('views',[]) if v.get('path') not in ('models','habits','modes')]
    def simplify(value):
        if isinstance(value,dict):return {k:simplify(v) for k,v in value.items()}
        if isinstance(value,list):return [simplify(v) for v in value]
        if isinstance(value,str):
            for old in ('/router-lab/modes','/router-lab/habits','/router-lab/models'):value=value.replace(old,'/router-lab/settings')
        return value
    config=simplify(config)
    existing={v.get('path') for v in config.get('views',[])}
    for path,title,icon,file in wanted:
        if path not in existing:config.setdefault('views',[]).append({'title':title,'path':path,'icon':icon,'panel':True,'cards':[{'type':'iframe','url':'/router_lab_ui/'+file,'aspect_ratio':'100%'}]})
    pipeline=next((p for p in client.websocket('assist_pipeline/pipeline/list')['pipelines'] if p['conversation_engine']=='conversation.router_lab'),None)
    if pipeline:
        voice=next(v for v in config['views'] if v.get('path')=='voice')
        voice['panel']=True
        voice['cards']=[{'type':'vertical-stack','cards':[
            {'type':'button','name':'开始语音对话','show_icon':False,'icon':'mdi:microphone','show_state':False,
             'tap_action':{'action':'assist','pipeline_id':pipeline['id'],'start_listening':True}},
            {'type':'iframe','url':'/router_lab_ui/voice.html','aspect_ratio':'90%'}]}]
    upgrade_trace_card(config)
    client.websocket('lovelace/config/save',url_path='router-lab',config=config)


def install(client):
    apps=client.supervisor('addons')['addons'];old={a['slug']:a.get('state') for a in apps}
    terminal_started=False
    try:
        for slug in ('core_configurator','core_ssh'):
            if slug not in old:
                print('Installing official helper: '+slug,flush=True)
                client.supervisor('addons/'+slug+'/install',{},timeout=900)
            info=client.supervisor('addons/'+slug+'/info')
            if slug=='core_ssh' and info.get('network',{}).get('22/tcp') not in (None,'',0):
                raise RuntimeError('This installer requires an internal-only terminal; it does not change SSH port settings.')
            if info['state']!='started':client.supervisor('addons/'+slug+'/start',{},timeout=120)
            if slug=='core_ssh':terminal_started=True
        for domain in ('router_lab','qwen_cloud_tts'):copy_integration(client,domain)
        with tempfile.TemporaryDirectory() as temporary:
            archive=Path(temporary)/'router_runtime_source.tar.gz';app=ROOT/'router_runtime'
            with tarfile.open(archive,'w:gz') as tar:
                for file in app.rglob('*'):
                    if file.is_file() and not any(p in file.parts for p in ('__pycache__','models')) and file.suffix not in ('.npz','.onnx','.bin'):tar.add(file,arcname='router_runtime/'+file.relative_to(app).as_posix())
            upload(client,archive,name='router_runtime_source.tar.gz')
            command(client,'mkdir -p /local_apps/router_runtime && tar -xzf /homeassistant/router_runtime_source.tar.gz -C /local_apps',60)
        client.supervisor('store/reload',{},timeout=120)
        slug='local_router_runtime'
        print('Building isolated compute app; first installation may take several minutes.',flush=True)
        action='/install'
        if slug in old:
            current=client.supervisor('addons/'+slug+'/info')
            action='/update' if current.get('version')!=current.get('version_latest') else '/rebuild'
        client.supervisor('addons/'+slug+action,{'backup':False} if action=='/update' else {},timeout=1800)
        if client.supervisor('addons/'+slug+'/info')['state']!='started':client.supervisor('addons/'+slug+'/start',{},timeout=120)
        print('Restarting HA to load integration source...',flush=True)
        try:client.request('/api/services/homeassistant/restart',{},timeout=30)
        except (http.client.RemoteDisconnected,ConnectionResetError):pass
        time.sleep(10);wait_core(client)
        if not client.websocket('config_entries/get',domain='router_lab'):
            result=client.request('/api/config/config_entries/flow',{'handler':'router_lab','show_advanced_options':False})
            if result.get('type')=='form':
                result=client.request('/api/config/config_entries/flow/'+result['flow_id'],{'router_url':'http://local-router-runtime:17861','model':'hybrid_v4','virtual_devices':True})
            if result.get('type')!='create_entry':raise RuntimeError('Cannot configure router integration')
        # Wait for the integration to finish registering its API, then create navigation only.
        for _ in range(60):
            try:client.websocket('router_lab/settings');break
            except RuntimeError:time.sleep(1)
        else:raise RuntimeError('Router integration did not finish loading')
        dashboard(client)
        command(client,'rm -f /homeassistant/router_runtime_source.tar.gz',30)
        print('Ready: '+client.base+'/router-lab/settings',flush=True)
        print('Enter your own LLM and voice API keys in that page. They were not copied by this installer.',flush=True)
    finally:
        if terminal_started and old.get('core_ssh')!='started':
            client.supervisor('addons/core_ssh/stop',{},timeout=120)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--ha-url',required=True);args=parser.parse_args()
    url=urlsplit(args.ha_url)
    if url.scheme not in ('http','https') or not url.hostname or url.username or url.password:parser.error('Provide a plain HA base URL with no credentials')
    token=getpass.getpass('HA long-lived token (not saved): ')
    client=HAClient(args.ha_url,token)
    try:install(client)
    except Exception as e:
        # Avoid printing request objects, tokens, or authenticated ingress URLs.
        print('Installation did not finish: '+str(e)[:300]);raise SystemExit(1) from None


if __name__=='__main__':main()
