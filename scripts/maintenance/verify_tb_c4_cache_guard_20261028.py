"""Exercise the exact cache guard with pinned bytes and an offline downloader."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PROJECT))
from experiments.scripts.tb_verifier_c4_asset import ASSET,SHA256,patch_source


def main():
    audit=Path('/mnt/data/agenttx-runtime/validation/tb-c4-verified-cache-20261028')
    pinned=audit/'pinned-c4-heldout.gz'
    assert hashlib.file_digest(pinned.open('rb'),'sha256').hexdigest()==SHA256
    original=Path('/mnt/data/agenttx-runtime/validation/tb-c4-bootstrap-20261020-r5/reshard-c4-data/run.sh').read_text()
    fixed=patch_source(original)
    first=fixed.index('if ! ( echo "'+SHA256)
    last=fixed.index('fi\n',first)+3
    guard=fixed[first:last]
    folder=audit/('guard-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    folder.mkdir();bin_dir=folder/'bin';bin_dir.mkdir()
    stub=bin_dir/'curl'
    stub.write_text('#!/bin/sh\nprintf called >> "$GUARD_CALLS"\nwhile [ "$#" -gt 0 ]; do\n if [ "$1" = --output ]; then shift; output="$1"; fi\n shift\ndone\ncp --reflink=auto "$GUARD_PINNED" "$output"\n')
    stub.chmod(0o755)
    rows=[]
    for case in ['complete','missing','corrupt']:
        asset=folder/(case+'.gz');calls=folder/(case+'.calls')
        if case=='complete':subprocess.run(['cp','--reflink=auto',str(pinned),str(asset)],check=True)
        if case=='corrupt':asset.write_bytes(b'corrupt-cache')
        script='set -eu\n'+guard.replace(ASSET,str(asset))+f'echo "{SHA256}  {asset}" | sha256sum -c -\n'
        env=dict(os.environ,PATH=str(bin_dir)+':/usr/bin:/bin',GUARD_CALLS=str(calls),GUARD_PINNED=str(pinned))
        r=subprocess.run(['bash','-c',script],env=env,capture_output=True,text=True,timeout=20)
        count=calls.read_text().count('called') if calls.exists() else 0
        assert r.returncode==0 and count==(0 if case=='complete' else 1),(case,r.returncode,r.stderr,count)
        rows.append(dict(case=case,returncode=r.returncode,downloader_calls=count,checksum_passed=True))
        asset.unlink()
    result=dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),returncode=0,
                no_model_calls=True,no_network_calls=True,active_workers_modified=False,
                pinned_sha256=SHA256,pinned_bytes=pinned.stat().st_size,cases=rows,
                original_script_sha256=hashlib.sha256(original.encode()).hexdigest(),
                fixed_script_sha256=hashlib.sha256(fixed.encode()).hexdigest(),
                assertions_changed=False,dataset_bytes_changed=False)
    (audit/'regression-result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':main()
