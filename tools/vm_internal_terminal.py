"""Installer transport via HA-authenticated ingress; never opens SSH ports."""
import http.cookiejar
import json
import re
import time
import urllib.parse
import uuid
from ha_client import HAClient


def command(client, script, timeout=60):
    import websocket
    info=client.supervisor('addons/core_ssh/info')
    assert info.get('network',{}).get('22/tcp') in (None,'',0), 'External SSH must remain disabled'
    session=client.supervisor('ingress/session',{})['session']
    host=urllib.parse.urlsplit(client.base).hostname
    client.cookies.set_cookie(http.cookiejar.Cookie(0,'ingress_session',session,None,False,host,False,False,'/',True,False,None,True,None,None,{}))
    ingress=info['ingress_url'].rstrip('/')
    token=client.request(ingress+'/token').get('token','')
    url=client.base.replace('http://','ws://').replace('https://','wss://')+ingress+'/ws'
    ws=websocket.create_connection(url,timeout=timeout,http_no_proxy=[host],subprotocols=['tty'],cookie='ingress_session='+session,suppress_origin=True)
    ws.send_binary(json.dumps({'AuthToken':token,'columns':240,'rows':50}).encode())
    # Wait for initial terminal screen, then suppress command echo.
    ws.recv();ws.send_binary(b'0stty -echo\r')
    time.sleep(.25)
    marker='ROUTER_INSTALL_'+uuid.uuid4().hex
    begin='BEGIN_'+marker
    ws.send_binary(('0printf "\\n'+begin+'\\n"\n'+script+'\nprintf "\\n'+marker+':%s\\n" "$?"\r').encode())
    output=b'';deadline=time.monotonic()+timeout
    try:
        while time.monotonic()<deadline:
            data=ws.recv()
            if isinstance(data,str):data=data.encode()
            if data[:1]==b'0':output+=data[1:]
            match=re.search((marker+r':(\d+)').encode(),output)
            if match:
                start=output.rfind(begin.encode(),0,match.start())
                text=output[start+len(begin):match.start()].decode('utf-8','replace') if start>=0 else ''
                # Discard ANSI sequences; never send secrets in scripts.
                text=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',text)
                if int(match.group(1)):raise RuntimeError('VM command failed: '+text[-1200:])
                return text[-6000:]
        raise TimeoutError('VM terminal timed out')
    finally:ws.close()


def upload(client, file, remote='/homeassistant', name=None):
    import urllib.request
    from pathlib import Path
    file=Path(file);name=name or file.name
    assert '/' not in name and '\\' not in name
    boundary='----router'+uuid.uuid4().hex
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="path"\r\n\r\n{remote}\r\n'
          f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+file.read_bytes()+f'\r\n--{boundary}--\r\n'.encode()
    assert len(body)<100*1024**2,'Split large files first'
    ingress=client.file_editor()
    req=urllib.request.Request(client.base+ingress+'/api/upload',body,headers={'Authorization':'Bearer '+client.token,'Content-Type':'multipart/form-data; boundary='+boundary})
    with client.opener.open(req,timeout=180) as response:r=json.load(response)
    if r.get('error'):raise RuntimeError('Authenticated file upload failed')

