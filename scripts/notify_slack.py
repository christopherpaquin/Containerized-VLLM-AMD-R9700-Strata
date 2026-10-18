#!/usr/bin/env python3
"""Send the explicitly requested readiness message through the existing Hermes bot."""
import json
import sys
import urllib.request
from pathlib import Path

env = {}
for line in Path('/home/cpaquin/.hermes/.env').read_text().splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        key, value = line.split('=', 1)
        env[key.strip()] = value.strip().strip('\"').strip("'")
message = Path(sys.argv[1]).read_text()
body = dict(channel=env['SLACK_HOME_CHANNEL'], text=message, unfurl_links=False, unfurl_media=False)
if len(sys.argv) > 2:
    body['thread_ts'] = sys.argv[2]
req = urllib.request.Request('https://slack.com/api/chat.postMessage', json.dumps(body).encode(),
    {'Content-Type':'application/json; charset=utf-8', 'Authorization':'Bearer '+env['SLACK_BOT_TOKEN']})
result = json.load(urllib.request.urlopen(req, timeout=30))
if not result.get('ok'):
    raise RuntimeError(result.get('error'))
print(json.dumps({'ok':True, 'channel':result['channel'], 'ts':result['ts']}))
