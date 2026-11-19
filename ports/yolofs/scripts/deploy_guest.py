#!/usr/bin/env python3
"""Copy this port and an immutable copy of the parent Python modules to RQ5."""

from pathlib import Path
import hashlib
import json
import shutil
import subprocess

project = Path(__file__).resolve().parents[3]
port = project / "ports/yolofs"
share = Path('/home/bfq/agenttx-workspace/rq5-20261113/yolofs-port-20261115')
stage = share / 'source'
stage.mkdir(parents=True, exist_ok=True)
for source, dest in [(project/'src/agenttx', stage/'src/agenttx'), (port, stage/'port')]:
    shutil.copytree(source, dest, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'results'))
manifest = {str(path.relative_to(stage)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in stage.rglob('*') if path.is_file() and path != stage/'sha256.json'}
(stage/'sha256.json').write_text(json.dumps(manifest, indent=2)+'\n')
ssh = ['ssh','-i','/home/bfq/agenttx-workspace/rq5-20261113/id_ed25519',
       '-o','BatchMode=yes','-o','UserKnownHostsFile=/home/bfq/agenttx-workspace/rq5-20261113/known_hosts',
       '-p','19225','root@127.0.0.1','python3 -']
code = '''from pathlib import Path
import shutil,json,hashlib
source=Path('/hostshare/yolofs-port-20261115/source')
target=Path('/opt/rq5/agenttx-yolofs-port')
shutil.copytree(source,target,dirs_exist_ok=True)
manifest=json.loads((target/'sha256.json').read_text())
for name,digest in manifest.items():
 assert hashlib.sha256((target/name).read_bytes()).hexdigest()==digest,name
print('Deployed and verified',len(manifest),'files to',target)
'''
subprocess.run(ssh,input=code,text=True,check=True)
print('SHA-256 manifest:',stage/'sha256.json')
