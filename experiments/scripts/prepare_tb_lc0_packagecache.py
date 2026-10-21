#!/usr/bin/env python3
"""Cache the original Meson wrap archives; enforce the upstream wrap hashes."""
from concurrent.futures import ThreadPoolExecutor
import configparser
import subprocess
from experiments.scripts.validate_tb_ready_local import DATA,save,digest,now
from experiments.workloads import terminal_bench_suite as tb


def main():
    output=DATA/'validation/tb-lc0-packagecache-20261020-r1';output.mkdir(exist_ok=False)
    cache=tb._asset_cache_dir();source=cache/'lc0-verifier-source'
    target=cache/'lc0-verifier-packagecache';target.mkdir(exist_ok=False)
    assets=[]
    for name in ['abseil-cpp','eigen','gtest']:
        wrap=configparser.ConfigParser(interpolation=None);wrap.read(source/'subprojects'/(name+'.wrap'))
        fields=wrap['wrap-file']
        for kind in ['source','patch']:
            if kind+'_filename' not in fields:continue
            filename=fields[kind+'_filename'];urls=[]
            fallback=fields.get('source_fallback_url') if kind=='source' else (
                'https://github.com/mesonbuild/wrapdb/releases/download/'+name+'_'+fields['wrapdb_version']+'/'+filename)
            for url in [fallback,fields[kind+'_url']]:
                if url and url.startswith('https://github.com/'):urls.append('https://gh-proxy.com/'+url)
            urls.extend(url for url in [fields[kind+'_url'],fallback] if url)
            assets.append(dict(file=filename,sha256=fields[kind+'_hash'],urls=list(dict.fromkeys(urls))))
    save(output/'registration.json',dict(started_at=now(),assets=assets,model_calls=0))
    def fetch(asset):
        name=asset['file'];partial=target/(name+'.partial');failures=[]
        for index,url in enumerate(asset['urls']):
            with (output/(name+'-'+str(index)+'.log')).open('w') as log:
                result=subprocess.run(['curl','--fail','--location','--connect-timeout','8','--max-time','120',
                    '--speed-time','20','--speed-limit','1024','--output',str(partial),url],stdout=log,stderr=subprocess.STDOUT,timeout=130)
            if result.returncode==0 and digest(partial)==asset['sha256']:
                partial.rename(target/name)
                return dict(file=name,sha256=asset['sha256'],url=url,bytes=(target/name).stat().st_size,prior_errors=failures)
            failures.append(dict(url=url,returncode=result.returncode,sha256=digest(partial) if partial.exists() else None))
            save(output/(name+'-errors.json'),failures)
        raise RuntimeError('Original pinned Meson asset unavailable: '+name)
    with ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(fetch,assets))
    proof=dict(finished_at=now(),assets=rows,model_calls=0,upstream_wrap_versions_preserved=True)
    save(output/'execution.json',proof);save(cache/'lc0-verifier-packagecache-proof.json',proof)


if __name__=='__main__':main()
