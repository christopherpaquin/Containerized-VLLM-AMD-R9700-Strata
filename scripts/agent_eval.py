#!/usr/bin/env python3
"""A bounded coding-agent trial against executable tests in an isolated container."""
import argparse
import collections
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--url', default='http://127.0.0.1:8080')
p.add_argument('--output', required=True)
p.add_argument('--image', default='b8a082f346d0')
p.add_argument('--steps', type=int, default=12)
p.add_argument('--effort', choices=['low','medium','high','xhigh'], default='xhigh')
a = p.parse_args()

fixtures = {
 'settings.py': 'TIMEOUT_MS = 5000\n',
 'client.py': 'from settings import TIMEOUT_MS\ndef fetch(session, url):\n    return session.get(url, timeout=TIMEOUT_MS)\n',
 'cli.py': 'import json\nimport sys\ndef main(argv):\n    with open(argv[0]) as stream:\n        config = json.load(stream)\n    print(config["name"])\n    return 0\nif __name__ == "__main__":\n    sys.exit(main(sys.argv[1:]))\n',
 'test_app.py': '''import contextlib, io, tempfile, unittest
from pathlib import Path
from unittest.mock import Mock
import client, cli
class TestApp(unittest.TestCase):
    def test_timeout_seconds(self):
        session=Mock(); client.fetch(session, "https://example.test")
        session.get.assert_called_once_with("https://example.test", timeout=5.0)
    def check_cli(self, text):
        with tempfile.TemporaryDirectory() as folder:
            f=Path(folder)/"config.json"; f.write_text(text)
            stdout,stderr=io.StringIO(),io.StringIO()
            with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
                code=cli.main([str(f)])
            return code,stdout.getvalue(),stderr.getvalue()
    def test_valid(self):
        code,out,err=self.check_cli('{"name":"scar"}')
        self.assertEqual((code,out,err),(0,"scar\\n",""))
    def test_empty(self):
        code,out,err=self.check_cli("")
        self.assertEqual(code,2); self.assertTrue(err.strip()); self.assertNotIn("Traceback",err)
    def test_malformed(self):
        code,out,err=self.check_cli("{")
        self.assertEqual(code,2); self.assertTrue(err.strip()); self.assertNotIn("Traceback",err)
    def test_missing_name(self):
        code,out,err=self.check_cli("{}")
        self.assertEqual(code,2); self.assertIn("name",err.lower())
'''
}
tools=[]
for name,description,props,required in [
 ('list_files','List repository files.',{},[]),
 ('read_file','Read a repository file.',{'path':{'type':'string'}},['path']),
 ('write_file','Replace an implementation file. Tests may not be changed.',{'path':{'type':'string'},'content':{'type':'string'}},['path','content']),
 ('run_tests','Run the executable regression tests.',{},[])]:
    tools.append(dict(type='function',function=dict(name=name,description=description,
                 parameters=dict(type='object',properties=props,required=required))))

with tempfile.TemporaryDirectory(prefix='strata-agent-eval-') as folder:
    root=Path(folder);root.chmod(0o755)
    for name,content in fixtures.items():
        (root/name).write_text(content)
    def tests():
        cmd=['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL',
             '--security-opt','no-new-privileges','--memory','512m','--cpus','2',
             '--user','1000:1000','--tmpfs','/tmp:rw,noexec,nosuid,size=32m',
             '-v',str(root)+':/work:ro','-w','/work','--entrypoint','/usr/bin/python3',
             a.image,'-B','-m','unittest','-v']
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
        return dict(exit_code=r.returncode,output=(r.stdout+r.stderr)[-10000:])
    initial=tests()
    assert initial['exit_code'] != 0, 'Fixture must fail before the agent edits it'
    messages=[dict(role='system',content='You are a coding agent. Inspect the repository, make minimal implementation changes, run tests, and finish. Use tools. Do not modify tests. Abandon hypotheses contradicted by evidence.'),
              dict(role='user',content='Fix this repository: HTTP timeouts are unexpectedly long and invalid CLI configuration raises tracebacks. An operator thinks DNS retries cause both problems. Use evidence to determine the causes. Preserve valid input behavior, keep configured timeout in milliseconds, and make CLI errors return code 2 with clear stderr. Run tests before finishing.')]
    history=[];counts=collections.Counter();start=time.monotonic();passed=False
    for step in range(a.steps):
        body=dict(model='scar-coder',messages=messages,tools=tools,max_tokens=4096,
                  reasoning_effort=a.effort,reasoning_budget_tokens=1536)
        headers={'Content-Type':'application/json'}
        if os.environ.get('STRATA_API_KEY'):
            headers['Authorization']='Bearer '+os.environ['STRATA_API_KEY']
        req=urllib.request.Request(a.url+'/v1/chat/completions',json.dumps(body).encode(),headers)
        response=json.load(urllib.request.urlopen(req,timeout=900))
        message=response['choices'][0]['message'];messages.append(message)
        calls=message.get('tool_calls') or []
        record=dict(step=step,message=message,finish_reason=response['choices'][0]['finish_reason'],results=[])
        history.append(record)
        if not calls:
            passed=tests()['exit_code']==0
            break
        for call in calls:
            fn=call['function'];signature=fn['name']+fn['arguments'];counts[signature]+=1
            try:
                args=json.loads(fn['arguments']);name=fn['name']
                if name=='list_files':
                    result=sorted(fixtures)
                elif name=='run_tests':
                    result=tests()
                elif name in ('read_file','write_file'):
                    filename=args['path']
                    if filename not in fixtures:
                        raise ValueError('Path must be one of the listed repository files')
                    if name=='read_file':
                        result=(root/filename).read_text()
                    else:
                        if filename.startswith('test_'):
                            raise ValueError('Tests cannot be changed')
                        (root/filename).write_text(args['content']);result='saved'
                else:
                    raise ValueError('Unknown tool')
            except Exception as e:
                result={'error':str(e)}
            record['results'].append(dict(tool=fn,result=result))
            messages.append(dict(role='tool',tool_call_id=call['id'],content=json.dumps(result)))
        Path(a.output).write_text(json.dumps(dict(initial=initial,history=history),indent=2)+'\n')
        print('step',step, [c['function']['name'] for c in calls],flush=True)
    final=tests();passed=final['exit_code']==0
    summary=dict(passed=passed,reasoning_effort=a.effort,elapsed_s=time.monotonic()-start,steps=len(history),
                 tool_calls=sum(counts.values()),repeated_calls=sum(n-1 for n in counts.values()),
                 initial=initial,final=final,history=history,
                 files={name:(root/name).read_text() for name in fixtures})
    Path(a.output).write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({k:summary[k] for k in ['passed','elapsed_s','steps','tool_calls','repeated_calls']}))
