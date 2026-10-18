#!/usr/bin/env python3
"""Refuse a silent configuration downgrade before starting the GPU runtime."""
import json
import sys
from pathlib import Path

c = json.loads(Path(sys.argv[1]).read_text())
a = c['args']
def value(flag):
    return a[a.index(flag)+1] if flag in a else None

required = {'--max-context': '262144', '--kv': 'int8', '--kv-resident': '32768',
            '--expert-cache': 'auto', '--vram-reserve-mib': '832', '--ple-io': 'direct', '--spec': '4'}
for flag, expected in required.items():
    assert value(flag) == expected, (flag, value(flag), expected)
assert any(q in value('--native') for q in ('IQ3_S', 'UD-Q4_K_XL')), 'Wrong quantization'
assert c['backend'] == 'hip'
assert c.get('parallel', 1) == 1
assert not c.get('vision')
assert c.get('reasoning_effort') == 'high', 'Quality default: high maps to xhigh; requests may override effort'
assert c.get('reasoning_budget_tokens') == 16384
assert c.get('reasoning_loop_recovery') == 'recover'
for flag in ('--mmap-experts', '--resident-experts', '--resident-budget-gib', '--vision',
             '--control-vector', '--control-vector-scaled', '--prob-draft'):
    assert flag not in a, flag
assert value('--rope-scaling') in (None, 'none'), 'Native context only'
assert c['env']['STRATA_ROUTE_TAIL_SKIP'] == '0'
assert c['env']['STRATA_SPEC_PROB'] == '0'
assert c['env']['STRATA_HC_Q8'] == '0', 'Preserve BF16 hyper-connection projections'
assert c.get('host') in ('127.0.0.1', '10.1.10.10')
assert c.get('host') == '127.0.0.1' or c.get('api_key'), 'LAN access requires an API key'
assert c['sampling'] == dict(temperature=1.0, top_p=0.95, top_k=20, min_p=0.0,
                             presence_penalty=0.0, repetition_penalty=1.0)
for flag in ('--native', '--pack', '--mtp'):
    assert Path(value(flag)).resolve().is_relative_to('/nvme/Strata-data'), flag
print('Quality baseline configuration verified')
