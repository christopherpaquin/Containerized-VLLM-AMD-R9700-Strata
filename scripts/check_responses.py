#!/usr/bin/env python3
"""Live smoke check for clients using the OpenAI Responses API."""
import argparse
import json
import os
import urllib.request
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--url', default='http://10.1.10.10:8080')
p.add_argument('--output', required=True)
a = p.parse_args()
body = dict(model='scar-coder', input='Reply exactly SCAR_READY.',
            reasoning={'effort':'none'}, max_output_tokens=64)
req = urllib.request.Request(a.url+'/v1/responses', json.dumps(body).encode(),
    {'Content-Type':'application/json', 'Authorization':'Bearer '+os.environ['STRATA_API_KEY']})
d = json.load(urllib.request.urlopen(req, timeout=300))
Path(a.output).write_text(json.dumps(d, indent=2)+'\n')
text = ''.join(part.get('text','') for item in d.get('output',[])
               for part in item.get('content',[]) if isinstance(part,dict))
assert 'SCAR_READY' in text, d
print('Responses API generation passed')
