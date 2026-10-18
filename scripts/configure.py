#!/usr/bin/env python3
"""Apply the reviewed quality baseline to setup's generated config."""
import json
import secrets
import shutil
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else '/opt/strata/strata-unsloth-ud-q4_k_xl.json')
c = json.loads(path.read_text())
backup = path.with_suffix('.setup-original.json')
if not backup.exists():
    shutil.copy2(path, backup)
a = c['args']
def set_arg(flag, value):
    if flag in a:
        a[a.index(flag)+1] = str(value)
    else:
        a.extend([flag, str(value)])

for flag, value in {'--max-context':262144, '--kv':'int8', '--kv-resident':32768,
                    '--expert-cache':'auto', '--vram-reserve-mib':832,
                    '--ple-io':'direct', '--spec':4}.items():
    set_arg(flag, value)
c.setdefault('env', {}).update(STRATA_ROUTE_TAIL_SKIP='0', STRATA_SPEC_PROB='0',
                                STRATA_SPEC_COUPLED='0', STRATA_SPEC_GUMBEL='0',
                                STRATA_HC_Q8='0')
c.update(reasoning_effort='high', reasoning_budget_tokens=16384,
         reasoning_loop_recovery='recover', parallel=1, open_browser=False,
         aliases=['scar-coder'], allowed_hosts=['scar.lab'],
         sampling=dict(temperature=1.0, top_p=0.95, top_k=20, min_p=0.0,
                       presence_penalty=0.0, repetition_penalty=1.0),
         log=f'/nvme/Strata-data/logs/{path.stem}.log')
key_path = Path('/home/cpaquin/.config/strata/api-key')
key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
if not key_path.exists():
    key_path.write_text(secrets.token_urlsafe(36)+'\n')
key_path.chmod(0o600)
c.update(host='10.1.10.10', api_key=key_path.read_text().strip())
Path('/nvme/Strata-data/logs').mkdir(exist_ok=True)
path.write_text(json.dumps(c, indent=2)+'\n')
path.chmod(0o600)
print('Configured', path)
