"""Opt-in download proxy for audited network retries in private task networks."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import yaml

BINARY = Path('/mnt/data/agenttx-runtime/tools/mihomo/v1.19.33/mihomo')
BINARY_SHA256 = '817f50b3ac6a8b1c4360c5cf2961906057f3a883d970ebbcb0028a01632b620f'
CONFIG = Path('/home/bfq/.config/agenttx/mihomo/task-config.yaml')
DOMAINS = ('github.com', 'githubusercontent.com', 'githubassets.com', 'github.io', 'huggingface.co', 'hf.co')
NO_PROXY = 'localhost,127.0.0.1,::1,10.249.44.127'
PROXY = 'http://127.0.0.1:17890'


def validate_config(config):
    expected = ['DST-PORT,8080,DIRECT'] + [
        'IP-CIDR,'+cidr+',DIRECT,no-resolve' for cidr in
        ('127.0.0.0/8','10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','169.254.0.0/16')
    ] + ['DOMAIN-SUFFIX,'+domain+',DOWNLOAD_RETRY' for domain in DOMAINS] + ['MATCH,DIRECT']
    assert config.get('mode') == 'rule' and config.get('rules') == expected
    assert config.get('bind-address') == '127.0.0.1' and config.get('allow-lan') is False
    assert config.get('mixed-port') == 17890
    assert not config.get('external-controller')
    assert not (config.get('tun') or {}).get('enable')
    assert not any(config.get(key) for key in ('redir-port','tproxy-port','listeners','rule-providers','sub-rules','script'))


def environment():
    return dict(HTTP_PROXY=PROXY,HTTPS_PROXY=PROXY,http_proxy=PROXY,https_proxy=PROXY,
        NO_PROXY=NO_PROXY,no_proxy=NO_PROXY,AGENTTX_NO_PROXY='0',AGENTTX_DOWNLOAD_PROXY_RETRY='1')


class RetryDownloadProxy:
    def __init__(self, network, folder, image):
        assert network.startswith('container:')
        self.network=network;self.folder=Path(folder);self.image=image;self.cid=None
        self.metadata={}

    def start(self):
        validate_config(yaml.safe_load(CONFIG.read_text()))
        assert hashlib.sha256(BINARY.read_bytes()).hexdigest()==BINARY_SHA256
        scope = ('user_authorized_domain_scoped_downloads'
                 if os.environ.get('AGENTTX_DOWNLOAD_PROXY_DEFAULT') == '1'
                 else 'explicit_network_failure_retry')
        self.metadata=dict(enabled=True,scope=scope,remote_proxy_domains=DOMAINS,
            other_domains='DIRECT',port_8080='DIRECT',model_api_bypasses_local_proxy=True,
            network=self.network,private_listener='127.0.0.1:17890',host_forwarding_modified=False,
            binary_sha256=BINARY_SHA256,config_sha256=hashlib.sha256(CONFIG.read_bytes()).hexdigest())
        self.cid=subprocess.check_output(['docker','run','-d','--pull=never',
            '--network='+self.network,'--cap-drop=ALL','--security-opt=no-new-privileges',
            '--user',f'{os.getuid()}:{os.getgid()}',
            '--read-only','--tmpfs','/tmp:rw,nosuid,nodev',
            '--mount',f'type=bind,src={BINARY},dst=/mihomo,readonly',
            '--mount',f'type=bind,src={CONFIG},dst=/config.yaml,readonly',
            '--entrypoint','/mihomo',self.image,'-d','/tmp/mihomo','-f','/config.yaml'],text=True,timeout=30).strip()
        probe="import socket,time\nfor i in range(20):\n try:\n  socket.create_connection(('127.0.0.1',17890),timeout=.5).close();break\n except OSError:time.sleep(.2)\nelse:raise RuntimeError('Download proxy listener did not become ready')"
        try:
            subprocess.run(['docker','exec',self.cid,'/usr/bin/python3','-c',probe],
                capture_output=True,text=True,check=True,timeout=15)
            (self.folder/'download-proxy.json').write_text(json.dumps(self.metadata,indent=2)+'\n')
        except Exception:
            self.close();raise
        return self

    def apply(self, command):
        result=list(command)
        at=result.index('--entrypoint')
        options=[]
        for name,value in environment().items():options+=['-e',name+'='+value]
        return result[:at]+options+result[at:]

    def close(self):
        if self.cid:
            logs=subprocess.run(['docker','logs',self.cid],capture_output=True,text=True,timeout=15)
            (self.folder/'download-proxy.log').write_text(logs.stdout+logs.stderr)
            subprocess.run(['docker','rm','-f',self.cid],capture_output=True,text=True,timeout=30)
            self.cid=None
