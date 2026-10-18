#!/usr/bin/env python3
"""Single-request streaming measurements; retain raw text and usage as evidence."""
import argparse
import gzip
import json
import os
import time
import urllib.request
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--url', default='http://127.0.0.1:8080')
p.add_argument('--output', required=True)
p.add_argument('--repeat', type=int, default=1)
p.add_argument('--max-tokens', type=int, default=256)
p.add_argument('--prompt', default='Write a Python function that safely parses journalctl JSON lines, counts messages by systemd unit, and ignores malformed lines. Include a short explanation and two examples.')
p.add_argument('--prompt-file')
p.add_argument('--thinking', default='none')
a = p.parse_args()
prompt = (gzip.open(a.prompt_file, 'rt').read() if a.prompt_file and a.prompt_file.endswith('.gz')
          else Path(a.prompt_file).read_text() if a.prompt_file else a.prompt)
results = []
for trial in range(a.repeat):
    body = dict(model='scar-coder', messages=[dict(role='user', content=prompt)],
                temperature=1.0, top_p=0.95, top_k=20, reasoning_effort=a.thinking,
                max_tokens=a.max_tokens, stream=True, stream_options={'include_usage': True})
    headers = {'Content-Type': 'application/json'}
    if os.environ.get('STRATA_API_KEY'):
        headers['Authorization'] = 'Bearer ' + os.environ['STRATA_API_KEY']
    req = urllib.request.Request(a.url + '/v1/chat/completions', json.dumps(body).encode(), headers)
    start = time.monotonic()
    first = None
    text, reasoning, usage, timings = '', '', {}, {}
    with urllib.request.urlopen(req, timeout=7200) as response:
        for line in response:
            if not line.startswith(b'data: '):
                continue
            raw = line[6:].strip()
            if raw == b'[DONE]':
                break
            event = json.loads(raw)
            if event.get('error'):
                raise RuntimeError(event['error'])
            usage = event.get('usage') or usage
            timings = event.get('timings') or timings
            for choice in event.get('choices', []):
                delta = choice.get('delta', {})
                content = delta.get('content') or ''
                thought = delta.get('reasoning_content') or ''
                if (content or thought) and first is None:
                    first = time.monotonic()
                text += content
                reasoning += thought
    end = time.monotonic()
    if first is None or not (text or reasoning):
        raise RuntimeError('No generated tokens received')
    tokens = usage.get('completion_tokens')
    result = dict(trial=trial, elapsed_s=end-start, ttft_s=first-start,
                  decode_tokens_s=(tokens-1)/(end-first) if tokens and tokens > 1 and end > first else None,
                  prompt_tokens_per_ttft_s=usage.get('prompt_tokens', 0)/(first-start),
                  usage=usage, timings=timings, text=text, reasoning=reasoning)
    results.append(result)
    Path(a.output).write_text(json.dumps(results, indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('text','reasoning')}), flush=True)
