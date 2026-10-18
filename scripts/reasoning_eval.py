#!/usr/bin/env python3
"""Synthetic, objectively scored reasoning probes. No model judges or generated code execution."""
import argparse
import itertools
import json
import os
import time
import urllib.request
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--url', default='http://10.1.10.10:8080')
p.add_argument('--output', default='benchmarks/strata-reasoning.json')
p.add_argument('--repeats', type=int, default=2)
p.add_argument('--only', help='Comma-separated case names; omit for all cases')
p.add_argument('--effort', choices=['low','medium','high','xhigh'], default='xhigh')
a = p.parse_args()
selected = set(a.only.split(',')) if a.only else None
results = []
system = ('Solve the task using only the supplied facts. Return one JSON object, without Markdown. '
          'Include the requested fields and a brief explanation. Do not invent missing facts.')

def ask(messages, seed):
    body = dict(model='scar-coder', messages=messages, temperature=1.0, top_p=0.95,
                top_k=20, min_p=0.0, presence_penalty=0.0, repetition_penalty=1.0,
                reasoning_effort=a.effort, reasoning_budget_tokens=16384,
                max_tokens=20000, seed=seed)
    headers = {'Content-Type':'application/json', 'Authorization':'Bearer '+os.environ['STRATA_API_KEY']}
    req = urllib.request.Request(a.url+'/v1/chat/completions', json.dumps(body).encode(), headers)
    started = time.monotonic()
    raw = json.load(urllib.request.urlopen(req, timeout=1200))
    message = raw['choices'][0]['message']
    content = message.get('content') or ''
    try:
        parsed = json.loads(content)
    except ValueError:
        parsed = None
    return raw, message, parsed, time.monotonic()-started

# All reference answers are calculated from trusted task data or explicit supplied facts.
jobs = [('A',4,8),('B',5,11),('C',3,7),('D',6,14),('E',2,5),('F',7,16),('G',4,10)]
feasible = []
for mask in itertools.product([False,True], repeat=len(jobs)):
    chosen = {jobs[i][0] for i,v in enumerate(mask) if v}
    cost = sum(j[1] for j in jobs if j[0] in chosen)
    benefit = sum(j[2] for j in jobs if j[0] in chosen)
    if cost <= 13 and (not ('D' in chosen) or 'B' in chosen) and not {'A','G'} <= chosen:
        feasible.append((benefit,cost,chosen))
best = max(x[0] for x in feasible)

def knapsack(d):
    chosen = set(d['jobs'])
    return len(chosen)==len(d['jobs']) and any(v==best and chosen==s for v,c,s in feasible) and d['benefit']==best

def ordering(d):
    order = d['order']
    if len(order)!=6 or set(order)!=set('ABCDEF'):
        return False
    i = {v:order.index(v) for v in order}
    return (i['A']<i['D'] and i['B']<i['D'] and i['D']<i['F'] and
            i['C']<i['E'] and i['E']<i['F'] and i['B']+1==i['C'] and i['A']>0)

def trace_reference():
    rows = [[1],[2]]
    alias = rows[:]
    alias[0].append(3)
    alias[1] = [4]
    rows.append(alias[0])
    alias[0] = alias[0]+[5]
    rows[2].append(6)
    return dict(rows=rows, alias=alias)

def faults_reference():
    signatures = {'A':{1,2},'B':{2,3},'C':{1,3,4},'D':{4,5},'E':{2,5}}
    observed = {1,2,3,4,5}
    matches=[]
    for n in (1,2):
        for subset in itertools.combinations(signatures,n):
            active=set()
            for f in subset:
                active ^= signatures[f]
            if active==observed:
                matches.append(set(subset))
    return matches

faults = faults_reference()
assert faults, 'The fault fixture must have a solution'
cases = [
 ('constrained-optimization',
  'Select jobs maximizing total benefit within cost 13. Each job is optional and can be selected once. '
  'D requires B; A and G cannot both be selected. Jobs (id,cost,benefit): '+json.dumps(jobs)+
  '. Return jobs (list of IDs) and benefit (integer).', knapsack,
  {'optimal_benefit':best,'optimal_sets':[sorted(s) for v,c,s in feasible if v==best]}),
 ('dependency-order',
  'Order A,B,C,D,E,F exactly once. A and B must precede D; D precedes F; C precedes E; E precedes F. '
  'B is immediately before C. A is not first. Return order as a list.', ordering,
  {'rule':'All seven supplied constraints checked directly; any valid solution accepted.'}),
 ('aliasing-code-trace',
  'Trace Python exactly; do not execute tools. What are rows and alias after this code? '
  'Return rows and alias as JSON arrays.\nrows=[[1],[2]]\nalias=rows[:]\nalias[0].append(3)\n'
  'alias[1]=[4]\nrows.append(alias[0])\nalias[0]=alias[0]+[5]\nrows[2].append(6)',
  lambda d: all(d[k]==v for k,v in trace_reference().items()), trace_reference()),
 ('base-rate-reasoning',
  'Exactly 2% of components are faulty. A test is positive for 90% of faulty components and 10% of healthy ones. '
  'A randomly chosen component tests positive. Return probability_faulty_given_positive as a number between 0 and 1, '
  'within 0.00001 of the exact answer.',
  lambda d: abs(d['probability_faulty_given_positive']-(.02*.9/(.02*.9+.98*.1)))<.00001,
  {'probability_faulty_given_positive':.02*.9/(.02*.9+.98*.1)}),
 ('fault-isolation-xor',
  'A system has either one or two faults from A,B,C,D,E. Each fault toggles the listed symptom bits; '
  'two faults combine by XOR, so a bit toggled twice is absent. '
  'A toggles [1,2]; B [2,3]; C [1,3,4]; D [4,5]; E [2,5]. '
  'Observed active bits are [1,2,3,4,5]. Return faults as a list; any consistent set is accepted.',
  lambda d: len(d['faults'])==len(set(d['faults'])) and set(d['faults']) in faults,
  {'consistent_sets':[sorted(s) for s in faults]}),
 ('insufficient-evidence',
  'Only these facts are known: the service was healthy at 10:00; a deployment occurred at 10:01; '
  'the service failed at 10:02. There are no other observations, interventions, or assumptions. '
  'Can we logically conclude the deployment caused the failure? Return conclusion as one of '
  '"entailed", "contradicted", "underdetermined". If underdetermined, provide two possible worlds '
  'consistent with the observations, one where the deployment caused failure and one where it did not; '
  'put them in worlds as two strings.',
  lambda d: d['conclusion']=='underdetermined' and len(d['worlds'])==2 and all(isinstance(x,str) and len(x)>15 for x in d['worlds']),
  {'conclusion':'underdetermined','worlds_note':'World descriptions require human review; format alone is not a proof.'}),
 ('critical-path',
  'Jobs run with unlimited workers once dependencies finish; durations are exact. '
  'A=3, B=5, C=4 after A, D=2 after A and B, E=6 after C and D, F=1 after D, G=2 after E and F. '
  'All roots start at time zero. Return earliest_finish for each job as an object and makespan as a number.',
  lambda d: d['earliest_finish']=={'A':3,'B':5,'C':7,'D':7,'E':13,'F':8,'G':15} and d['makespan']==15,
  {'earliest_finish':{'A':3,'B':5,'C':7,'D':7,'E':13,'F':8,'G':15},'makespan':15}),
]

hard_jobs = [('A',5,4,13),('B',6,3,15),('C',4,5,12),('D',7,4,19),
             ('E',3,2,8),('F',8,5,23),('G',5,3,14),('H',4,4,13),
             ('I',6,2,17),('J',3,5,11),('K',7,3,20),('L',2,2,6)]
hard_feasible=[]
for mask in itertools.product([False,True],repeat=len(hard_jobs)):
    s={hard_jobs[i][0] for i,v in enumerate(mask) if v}
    cpu=sum(j[1] for j in hard_jobs if j[0] in s)
    ram=sum(j[2] for j in hard_jobs if j[0] in s)
    value=sum(j[3] for j in hard_jobs if j[0] in s)
    if (cpu<=23 and ram<=14 and (not 'H' in s or 'B' in s)
            and (not 'K' in s or {'D','E'}<=s) and not {'F','G'}<=s
            and not {'A','I'}<=s and len(s&{'C','J','L'})<=2):
        hard_feasible.append((value,s))
hard_best=max(v for v,s in hard_feasible)
def hard_check(d):
    s=set(d['jobs'])
    return len(s)==len(d['jobs']) and d['benefit']==hard_best and any(v==hard_best and s==other for v,other in hard_feasible)
cases.append(('multi-resource-optimization',
    'Find the globally optimal subset of jobs maximizing total benefit. Jobs may each be selected once or omitted. '
    'CPU total must be at most 23; RAM total at most 14. H requires B. K requires BOTH D and E. '
    'F and G cannot both be chosen. A and I cannot both be chosen. At most two of C,J,L may be chosen. '
    'Data tuples are (id,CPU,RAM,benefit): '+json.dumps(hard_jobs)+
    '. Return jobs as a list and benefit as an integer. Verify feasibility and global optimality.',
    hard_check,{'optimal_benefit':hard_best,'optimal_sets':[sorted(s) for v,s in hard_feasible if v==hard_best]}))

def save():
    Path(a.output).write_text(json.dumps(dict(settings=dict(temperature=1.0,reasoning_effort=a.effort,reasoning_budget=16384,
         max_tokens=20000,repeats=a.repeats),results=results),indent=2)+'\n')

for repeat in range(a.repeats):
    for name,prompt,check,reference in cases:
        if selected is not None and name not in selected:
            continue
        raw,message,d,elapsed = ask([dict(role='system',content=system),dict(role='user',content=prompt)],42+repeat)
        try:
            passed = isinstance(d,dict) and bool(check(d))
        except (KeyError,TypeError,ValueError):
            passed = False
        results.append(dict(case=name,repeat=repeat,passed=passed,elapsed_s=elapsed,
                            parsed=d,reference=reference,response=raw))
        save();print(name,repeat,'PASS' if passed else 'FAIL',round(elapsed,1),flush=True)
    if selected is not None and 'update-hypothesis' not in selected:
        continue
    messages=[dict(role='system',content=system),dict(role='user',content=
      'Exactly one cause is true: A (bad executable path), B (unreadable configuration), or C (busy port). '
      'Test 1 proves the executable path is correct and executable. There is no other evidence. '
      'Return possible_causes as the full list of causes still possible.')]
    stages=[]
    for turn in range(2):
        raw,message,d,elapsed=ask(messages,42+repeat)
        expected=['B','C'] if turn==0 else ['C']
        try:
            passed=isinstance(d,dict) and sorted(d['possible_causes'])==expected
        except (KeyError,TypeError):
            passed=False
        stages.append(dict(passed=passed,parsed=d,expected=expected,response=raw,elapsed_s=elapsed))
        messages.append(message)
        if turn==0:
            messages.append(dict(role='user',content='New test: running as the service user, the exact configuration file is successfully opened and read. '
                  'This test is conclusive. The original exactly-one-cause assumption still holds. Return the updated possible_causes.'))
    passed=all(s['passed'] for s in stages)
    results.append(dict(case='update-hypothesis',repeat=repeat,passed=passed,stages=stages,
                        elapsed_s=sum(s['elapsed_s'] for s in stages)))
    save();print('update-hypothesis',repeat,'PASS' if passed else 'FAIL',flush=True)
print('SCORE',sum(r['passed'] for r in results),'/',len(results),flush=True)
