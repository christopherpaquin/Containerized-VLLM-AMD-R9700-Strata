#!/usr/bin/env python3
"""Resumable range download of pinned model shards, verified against LFS SHA-256."""
import argparse
import concurrent.futures
import hashlib
import json
import os
import threading
import time
import urllib.request
from pathlib import Path

MODELS = {
    'IQ3_S': dict(
        repo='ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF',
        revision='ed59f92082b1e93c0e96d60a8b11aab089b52f09',
        subpath='IQ3_S',
        root=Path('/nvme/Strata-data/models/IQ3_S'),
        manifest=Path('/nvme/Strata-data/model-manifest.json'),
    ),
    'UD-Q4_K_XL': dict(
        repo='unsloth/Qwen3.8-Flash-Next-GGUF',
        revision='38bb39ee97821de2c9009abb7e93950eec396e66',
        subpath='UD-Q4_K_XL',
        root=Path('/nvme/Strata-data/models/unsloth-UD-Q4_K_XL'),
        manifest=Path('/nvme/Strata-data/model-manifest-ud-q4_k_xl.json'),
    ),
}
parser = argparse.ArgumentParser()
parser.add_argument('--model', choices=sorted(MODELS), default='UD-Q4_K_XL')
parser.add_argument('--shard', default='')
parser.add_argument('--workers', type=int, default=8)
args = parser.parse_args()
spec = MODELS[args.model]
repo = spec['repo']
revision = spec['revision']
root = spec['root']
root.mkdir(parents=True, exist_ok=True)
meta = json.load(urllib.request.urlopen(f'https://huggingface.co/api/models/{repo}/tree/{revision}/{spec["subpath"]}'))
if not args.shard:
    spec['manifest'].write_text(json.dumps(dict(repo=repo, revision=revision, files=meta), indent=2)+'\n')
lock = threading.Lock()
for item in meta:
    if item['type'] != 'file' or not item['path'].endswith('.gguf'):
        continue
    if args.shard and args.shard not in item['path']:
        continue
    dst = root / Path(item['path']).name
    if dst.exists() and dst.with_name(dst.name+'.done').exists():
        print('Already complete:', dst.name, flush=True)
        continue
    size = item['size']
    part = dst.with_name(dst.name+'.part')
    temp = dst.with_name(dst.name+'.parallel')
    statefile = dst.with_name(dst.name+'.ranges.json')
    if statefile.exists():
        state = json.loads(statefile.read_text())
        state['complete'] = sorted(set(state['complete']))
    else:
        have = part.stat().st_size if part.exists() else 0
        state = dict(prefix=have, complete=[])
        if part.exists():
            part.rename(temp)
        statefile.write_text(json.dumps(state))
    fd = os.open(temp, os.O_CREAT | os.O_RDWR, 0o644)
    os.ftruncate(fd, size)
    chunk = 64*1024*1024
    ranges = [(s, min(s+chunk, size)-1) for s in range(state['prefix'], size, chunk)
              if s not in state['complete']]
    def fetch(bounds):
        start, end = bounds
        for attempt in range(6):
            try:
                url = f'https://huggingface.co/{repo}/resolve/{revision}/{item["path"]}?range_start={start}'
                req = urllib.request.Request(url, headers={'Range': f'bytes={start}-{end}'})
                with urllib.request.urlopen(req, timeout=120) as response:
                    expected = f'bytes {start}-{end}/{size}'
                    if response.status != 206 or response.headers.get('Content-Range') != expected:
                        raise RuntimeError(f'Bad range: {response.status} {response.headers.get("Content-Range")} expected {expected}')
                    offset = start
                    while data := response.read(4*1024*1024):
                        if offset+len(data) > end+1:
                            raise RuntimeError('Range overflow')
                        view = memoryview(data)
                        while view:
                            written = os.pwrite(fd, view, offset)
                            offset += written
                            view = view[written:]
                    if offset != end+1:
                        raise RuntimeError('Incomplete range')
                with lock:
                    state['complete'].append(start)
                    atomic = statefile.with_suffix('.tmp')
                    atomic.write_text(json.dumps(state))
                    atomic.replace(statefile)
                    count = len(state['complete'])
                    if count % 8 == 0:
                        print(dst.name, f'{min(size,state["prefix"]+count*chunk)/1e9:.2f}/{size/1e9:.2f} GB', flush=True)
                return
            except Exception as e:
                if attempt == 5:
                    raise
                print('Retry', start, str(e), flush=True)
                time.sleep(2*(attempt+1))
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(fetch, ranges))
        os.fsync(fd)
    finally:
        os.close(fd)
    print('Verifying SHA-256:', dst.name, flush=True)
    h = hashlib.sha256()
    with temp.open('rb') as f:
        while data := f.read(16*1024*1024):
            h.update(data)
    assert h.hexdigest() == item['lfs']['oid'], (dst.name, h.hexdigest(), item['lfs']['oid'])
    temp.rename(dst)
    dst.with_name(dst.name+'.done').write_text(f'sha256 {h.hexdigest()}\n')
    print('Verified:', dst.name, h.hexdigest(), flush=True)
