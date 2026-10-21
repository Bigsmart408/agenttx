#!/usr/bin/env python3
"""Cache the two original CPU wheels, verified against upstream index hashes."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote
from experiments.scripts.repair_tb_missing_images_local import download
from experiments.scripts.validate_tb_ready_local import DATA,save,now
from experiments.workloads import terminal_bench_suite as tb


def main():
    output=DATA/'validation/tb-lc0-cpu-wheel-cache-20261020-r1';output.mkdir(exist_ok=False)
    cache=tb._asset_cache_dir();assert cache.resolve().is_relative_to(DATA)
    save(output/'registration.json',dict(started_at=now(),wheels=tb._LC0_CPU_WHEELS,
        identity_proof=str(DATA/'validation/tb-lc0-cpu-mirror-proof-20261020-r1/proof.json'),
        original_partial_prefix_reused=True,model_calls=0))
    def fetch(item):
        name,expected=item;folder=output/name;folder.mkdir()
        return download(name,'https://mirrors.nju.edu.cn/pytorch/whl/cpu/'+quote(name),
            expected,cache,folder,lambda **fields:save(folder/'progress.json',dict(checked_at=now(),**fields)))
    with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(fetch,tb._LC0_CPU_WHEELS.items()))
    save(output/'execution.json',dict(finished_at=now(),wheels=rows,model_calls=0))


if __name__=='__main__':main()
