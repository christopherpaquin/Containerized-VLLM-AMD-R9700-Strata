import sys,importlib.util,tempfile,pathlib,os,json,logging
spec=importlib.util.spec_from_file_location('generated','/fixture/generated-scanner.py');m=importlib.util.module_from_spec(spec);sys.modules['generated']=m;spec.loader.exec_module(m)
checks=[]
with tempfile.TemporaryDirectory() as d:
 root=pathlib.Path(d);(root/'sub').mkdir();(root/'blocked').mkdir()
 for name,size in [('big',3*(1<<30)),('sub/medium',2*(1<<30)),('threshold',1<<30),('small',100),('blocked/hidden',4*(1<<30))]:
  with (root/name).open('wb') as f:f.truncate(size)
 (root/'sub/loop').symlink_to(root,target_is_directory=True);(root/'broken').symlink_to(root/'missing');os.mkfifo(root/'fifo');(root/'blocked').chmod(0)
 expected=[str(root/'big'),str(root/'sub/medium')]
 try:
  for name,call in [
   ('sorted_strict_threshold_regular_only',lambda:[f.path for f in m.scan_large_files(str(root))]),
   ('optional_follow_symlinks_cycle_guard',lambda:[f.path for f in m.scan_large_files(str(root),follow_symlinks=True)]),
   ('external_sort_forced_multiple_chunks',lambda:list(m.scan_large_files_external(str(root),max_buffer_size=1)))
  ]:
   try:
    actual=call();checks.append(dict(name=name,passed=actual==expected,actual=[pathlib.Path(p).name for p in actual]))
   except Exception as e:checks.append(dict(name=name,passed=False,error=repr(e)))
  logged=[]
  class H(logging.Handler):
   def emit(self,r):logged.append(r.getMessage())
  m.log.addHandler(H())
  actual=[f.path for f in m.scan_large_files(str(root/'blocked'))]
  checks.append(dict(name='unreadable_directory_reports_and_skips',passed=not actual and any('no read permission' in msg or 'PermissionError' in msg for msg in logged),logged=logged))
 finally:(root/'blocked').chmod(0o700)
print(json.dumps(dict(uid=os.getuid(),checks=checks),indent=2))
