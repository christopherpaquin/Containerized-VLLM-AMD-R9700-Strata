#!/usr/bin/env python3
"""Exercise live tool round trips and engineering diagnostics; save reviewable replies."""
import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--url', default='http://127.0.0.1:8080')
p.add_argument('--output', required=True)
a = p.parse_args()
results = []

def request(messages, **kwargs):
    body = dict(model='scar-coder', messages=messages, max_tokens=2048,
                reasoning_effort='xhigh', reasoning_budget_tokens=1024, **kwargs)
    headers = {'Content-Type':'application/json'}
    if os.environ.get('STRATA_API_KEY'):
        headers['Authorization'] = 'Bearer '+os.environ['STRATA_API_KEY']
    req = urllib.request.Request(a.url+'/v1/chat/completions', json.dumps(body).encode(), headers)
    return json.load(urllib.request.urlopen(req, timeout=900))

def save(label, start, response, passed=None):
    results.append(dict(case=label, elapsed_s=time.monotonic()-start, response=response,
                        passed=passed, note='Diagnostic answers require review; no automatic quality claim.'))
    Path(a.output).write_text(json.dumps(results, indent=2)+'\n')
    print(label, 'recorded', flush=True)

tools = [dict(type='function', function=dict(name='read_service_status',
    description='Read the service status for diagnosis.',
    parameters=dict(type='object', properties={'unit':{'type':'string'}}, required=['unit'])))]
messages = [dict(role='user', content='Use read_service_status to inspect demo.service. Do not guess its state.')]
start = time.monotonic()
r = request(messages, tools=tools)
message = r['choices'][0]['message']
calls = message.get('tool_calls') or []
assert len(calls) == 1 and calls[0]['function']['name'] == 'read_service_status', r
assert json.loads(calls[0]['function']['arguments'])['unit'] == 'demo.service', r
save('tool-call', start, r, True)
messages.append(message)
messages.append(dict(role='tool', tool_call_id=calls[0]['id'],
                     content='ActiveState=failed; Result=exit-code; status=203/EXEC; ExecStart=/opt/demo/bin/start; journal: No such file or directory'))
start = time.monotonic()
r = request(messages, tools=tools)
assert r['choices'][0]['message'].get('content'), r
save('tool-result-followup', start, r, True)

cases = {
 'systemd-wrong-hypothesis': 'An operator blames port 8080 for demo.service failing. Journal: Failed at step EXEC spawning /opt/demo/start: No such file or directory; status=203/EXEC. The port is free. Explain the supported cause and next evidence to gather. Do not edit anything.',
 'container-runtime': 'A ROCm container reports no GPU. Host rocminfo sees gfx1201. docker inspect shows Devices=[] and GroupAdd=[]. Provide minimal checks and a Compose device/group fix without privileged mode.',
 'ansible': 'An Ansible task template writes /etc/systemd/system/demo.service and notifies restart demo. The handler only uses service state=restarted. After ExecStart changes systemd warns unit changed on disk. Show the minimal handler correction and explain ordering.',
 'bash': 'A Bash script uses for f in $(find "$dir" -name "*.log"); do cat "$f"; done. Filenames contain newlines and spaces. Give a safe streaming replacement and explain why quoting alone does not fix the original.',
 'python': 'Python cache code: def get(k, cache={}): if k not in cache: cache[k]=load(k); return cache[k]. Requirement: caches must be isolated per caller instance, but persist between get calls on the same instance. Propose a class and tests. Do not just replace {} with None and lose persistence.',
 'logs': 'Logs: 10:00 GET /health 200; 10:01 postgres ERROR remaining connection slots are reserved; 10:01 API timeout; 10:02 DNS lookup successful. Operator suggests changing DNS. Explain which evidence matters, what to inspect next, and what changes to avoid without evidence.',
 'dependency': 'systemd runs /opt/app/.venv/bin/python /opt/app/main.py. pip show requests from the SSH shell succeeds; service says ModuleNotFoundError: requests. Give commands to identify the interpreter mismatch and install only into the correct environment.',
 'multi-file-debugging': 'config.py defines TIMEOUT_MS=5000. client.py passes timeout=TIMEOUT_MS to requests.get. tests/test_client.py mocks requests.get without asserting timeout. Explain the bug, minimal changes across these files, and a meaningful regression test.',
 'evidence-before-editing': 'A deployment fails after three retries of increasing HTTP timeout. Each log says PermissionError opening /var/lib/app/state.json. The operator asks to increase timeout again. Decide the next action, exact evidence needed, and when to stop retrying.',
 'repo-change': 'A repo has loader.py reading YAML using yaml.safe_load, validator.py expecting dict, and cli.py printing tracebacks. Empty YAML returns None and crashes. Propose a minimal multi-file change with exit code 2, a clear error and tests for empty, malformed and valid YAML. Preserve valid input behavior.'
}
for label, prompt in cases.items():
    start = time.monotonic()
    try:
        save(label, start, request([dict(role='user', content=prompt)]))
    except Exception as e:
        save(label, start, {'error':str(e)}, False)
