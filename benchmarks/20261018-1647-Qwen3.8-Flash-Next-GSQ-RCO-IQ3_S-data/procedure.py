import json,time,threading,subprocess,urllib.request,hashlib,os
from pathlib import Path
from datetime import datetime
root=Path('/nvme/Strata-data/benchmarks')/(datetime.now().strftime('%Y%m%d-%H%M')+'-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-data');root.mkdir()
Path('/tmp/strata-report-data-path').write_text(str(root))
key=Path('/home/cpaquin/.config/strata/api-key').read_text().strip()
def api(p):
 return json.load(urllib.request.urlopen(urllib.request.Request('http://10.1.10.10:8080'+p,headers={'Authorization':'Bearer '+key}),timeout=10))
def cmd(c): return subprocess.run(c,shell=True,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT).stdout
pid=int(cmd('systemctl show strata -p MainPID --value')); children=[int(x) for x in cmd(f'pgrep -P {pid}').split()];pids=[pid]+children
prompts=[('systemd','A RHEL server has a custom systemd service. The executable works correctly when launched manually as root, but the service fails immediately when started with systemctl. You have not yet determined the cause. Develop a troubleshooting strategy and explain what evidence you would gather before changing the unit file. Consider user identity, environment variables, working directory, permissions, SELinux, dependencies, filesystem paths, and journal logs.'),('coding','You are debugging an existing Python application. A function is supposed to recursively scan a directory and return all files larger than 1 GiB sorted from largest to smallest. The current implementation occasionally crashes on unreadable directories, follows symlink loops, and consumes too much memory on very large trees. Describe how you would diagnose the current implementation before changing it, then provide a robust implementation.'),('recovery','A Linux application cannot connect to a local PostgreSQL server. Another engineer believes the firewall is blocking the connection and recommends immediately disabling firewalld. Assume that hypothesis may be wrong. Explain how you would validate or reject it before making changes, and continue troubleshooting based on the evidence you would collect.')]
config=Path('/opt/strata/strata-iq3_s.json'); original=config.read_bytes(); sanitized=json.loads(original);sanitized.pop('api_key',None)
# recursive credential redaction
for k in list(sanitized):
 if 'key' in k.lower(): sanitized[k]='[REDACTED]'
(root/'config.json').write_text(json.dumps(sanitized,indent=2))
for p in ['/props','/metrics','/v1/status','/v1/models','/settings']:
 try: (root/(p.strip('/').replace('/','-')+'-before.json')).write_text(json.dumps(api(p),indent=2))
 except Exception as e: print(p,type(e).__name__)
commands={'free':'free -h','processes':'ps -eo pid,ppid,cmd,rss,vsz,%mem --sort=-rss | head -30','df':'df -hT /nvme','storage':'du -h -d 3 /nvme/Strata-data; find /nvme/Strata-data/mtp /nvme/Strata-data/packs -maxdepth 3 -type f -printf "%p %s bytes\\n"','service':'systemctl show strata -p MainPID -p NRestarts -p ActiveEnterTimestamp -p ExecStart -p Environment','versions':'git -C /opt/strata describe --tags --always; git -C /opt/strata rev-parse HEAD; cat /opt/rocm/.info/version; uname -a; lscpu','gpu':'rocm-smi --showproductname --showuse --showmeminfo vram --showclocks --showpower --showtemp --showperflevel --showreplaycount --json','vmstat':'vmstat 1 3','kernel':"sudo journalctl -k --since '2026-10-18 16:37:00' --no-pager",'startup':"journalctl -u strata --since '2026-10-18 16:37:00' --no-pager"}
for name,c in commands.items(): (root/(name+'-before.txt')).write_text(cmd(c))
for p in pids:
 for f in ['status','smaps_rollup','io','maps']:
  try:(root/f'pid-{p}-{f}-before.txt').write_text(Path(f'/proc/{p}/{f}').read_text())
  except Exception: pass
 env=Path(f'/proc/{p}/environ').read_bytes().split(b'\0');(root/f'pid-{p}-strata-env.txt').write_text('\n'.join(x.decode() for x in env if x.startswith(b'STRATA_')))
def sample():
 m={l.split(':')[0]:int(l.split()[1]) for l in Path('/proc/meminfo').read_text().splitlines()};v={l.split()[0]:int(l.split()[1]) for l in Path('/proc/vmstat').read_text().splitlines()}
 processes={}
 for p in pids:
  s={}
  for l in Path(f'/proc/{p}/status').read_text().splitlines():
   if l.startswith(('VmRSS:','VmSize:','VmSwap:','RssAnon:','RssFile:')):s[l.split(':')[0]]=int(l.split()[1])
  s['io']=Path(f'/proc/{p}/io').read_text();processes[p]=s
 gpu={}
 base=Path('/sys/class/drm/card1/device')
 if not base.exists():base=next(Path('/sys/class/drm').glob('card[0-9]/device'))
 for f in ['mem_info_vram_used','mem_info_vram_total','gpu_busy_percent','pp_dpm_sclk','pp_dpm_mclk']:
  try:gpu[f]=(base/f).read_text().strip()
  except:pass
 for hw in base.glob('hwmon/hwmon*'):
  for f in ['temp1_input','temp2_input','power1_average','power1_cap']:
   try:gpu[f]=(hw/f).read_text().strip()
   except:pass
 return dict(at=time.time(),mem=m,vmstat={k:v[k] for k in ['pswpin','pswpout','pgmajfault','pgscan_kswapd','pgscan_direct'] if k in v},processes=processes,gpu=gpu)
for name,prompt in prompts:
 before=api('/metrics');samples=[];stop=threading.Event()
 def monitor():
  while not stop.is_set():
   try:s=sample();s['live']=api('/metrics').get('live');samples.append(s)
   except Exception as e:print(type(e).__name__,flush=True)
   stop.wait(.5)
 t=threading.Thread(target=monitor);t.start()
 body=dict(model='scar-coder',messages=[dict(role='user',content=prompt)],stream=True,stream_options={'include_usage':True},max_tokens=24576,seed=42)
 start=time.monotonic();first=None;ff=None;lastreason=None;usage={};timings={};answer='';rc=0;finish=None
 req=urllib.request.Request('http://10.1.10.10:8080/v1/chat/completions',json.dumps(body).encode(),{'Authorization':'Bearer '+key,'Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=1800) as response:
   for line in response:
    if not line.startswith(b'data: '):continue
    raw=line[6:].strip()
    if raw==b'[DONE]':break
    e=json.loads(raw)
    if e.get('error'):raise RuntimeError(str(e['error']))
    usage=e.get('usage') or usage;timings=e.get('timings') or timings
    for c in e.get('choices',[]):
     d=c.get('delta',{});a=d.get('content') or '';r=d.get('reasoning_content') or '';now=time.monotonic()
     if (a or r) and first is None:first=now
     if r:rc+=len(r);lastreason=now
     if a:
      if ff is None:ff=now
      answer+=a
     if c.get('finish_reason'):finish=c['finish_reason']
 finally:
  end=time.monotonic();stop.set();t.join()
 after=api('/metrics');result=dict(name=name,prompt=prompt,request=body,start_unix=time.time()-(end-start),elapsed_s=end-start,ttft_s=first-start if first else None,first_final_s=ff-start if ff else None,reasoning_stream_duration_s=lastreason-first if lastreason and first else None,final_stream_duration_s=end-ff if ff else None,reasoning_characters_observed=rc,usage=usage,timings=timings,finish=finish,answer=answer,metrics_before=before,metrics_after=after,samples=samples)
 (root/(name+'.json')).write_text(json.dumps(result,indent=2));print(name,json.dumps({k:result[k] for k in ['elapsed_s','ttft_s','first_final_s','usage','timings','finish']}),flush=True)
for n,c in [('free','free -h'),('vmstat','vmstat 1 3'),('kernel',commands['kernel']),('startup',commands['startup'])]:(root/(n+'-after.txt')).write_text(cmd(c))
(root/'integrity.json').write_text(json.dumps(dict(config_sha256_before=hashlib.sha256(original).hexdigest(),config_sha256_after=hashlib.sha256(config.read_bytes()).hexdigest(),pid_before=pid,pid_after=int(cmd('systemctl show strata -p MainPID --value')),restarts=cmd('systemctl show strata -p NRestarts --value'))))
print('COMPLETE',root,flush=True)
