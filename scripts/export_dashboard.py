#!/usr/bin/env python3
"""Export an allowlisted public settings snapshot; never export the service API key."""
import json
import sys
from pathlib import Path

cfg_path = Path(sys.argv[1] if len(sys.argv) > 1 else '/opt/strata/strata-unsloth-ud-q4_k_xl.json')
c = json.loads(cfg_path.read_text())
a = c['args']
def value(flag):
    return a[a.index(flag)+1] if flag in a else None

data = {k:c.get(k) for k in ['reasoning_effort','reasoning_budget_tokens','reasoning_loop_recovery']}
data.update(service='strata.service', ple_io=value('--ple-io'),
            vram_reserve_mib=int(value('--vram-reserve-mib')),
            route_tail_skip=c['env'].get('STRATA_ROUTE_TAIL_SKIP'),
            hc_q8=c['env'].get('STRATA_HC_Q8'), spec_prob=c['env'].get('STRATA_SPEC_PROB'))
root=Path('/nvme/Strata-data/dashboard');root.mkdir(exist_ok=True,mode=0o755)
temp=root/'runtime.tmp'
temp.write_text(json.dumps(data,indent=2)+'\n');temp.chmod(0o644)
temp.replace(root/'runtime.json')
print('Exported public dashboard settings')
