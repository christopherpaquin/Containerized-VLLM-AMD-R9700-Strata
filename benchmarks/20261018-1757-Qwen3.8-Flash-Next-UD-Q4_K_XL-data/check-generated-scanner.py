import importlib.util,tempfile,pathlib,os,json
spec=importlib.util.spec_from_file_location('generated','/fixture/generated-scanner.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
checks=[]
with tempfile.TemporaryDirectory() as d:
 root=pathlib.Path(d);(root/'sub').mkdir();(root/'blocked').mkdir()
 for name,size in [('big',3*(1<<30)),('sub/medium',2*(1<<30)),('threshold',1<<30),('small',100),('blocked/hidden',4*(1<<30))]:
  with (root/name).open('wb') as f:f.truncate(size)
 (root/'sub/loop').symlink_to(root,target_is_directory=True);(root/'broken').symlink_to(root/'missing');os.mkfifo(root/'fifo');(root/'blocked').chmod(0)
 expected=[str(root/'big'),str(root/'sub/medium')]
 try:
  for name,call in [('sorted_strict_threshold_regular_only',lambda:m.files_larger_than_gib(str(root))),('optional_follow_symlinks_cycle_guard',lambda:m.files_larger_than_gib(str(root),include_symlinked_files=True)),('external_sort_forced_multiple_chunks',lambda:list(m.iter_sorted_large_files_externally(str(root),max_buffered=1)))]:
   try:
    actual=call();checks.append(dict(name=name,passed=actual==expected,actual=[pathlib.Path(p).name for p in actual]))
   except Exception as e:checks.append(dict(name=name,passed=False,error=repr(e)))
  errors=[];actual=list(m.iter_large_files(str(root/'blocked'),on_error=lambda p,e:errors.append(type(e).__name__)));checks.append(dict(name='unreadable_directory_reports_and_skips',passed=not actual and 'PermissionError' in errors,errors=errors))
 finally:(root/'blocked').chmod(0o700)
print(json.dumps(dict(uid=os.getuid(),checks=checks),indent=2));assert all(x['passed'] for x in checks)
