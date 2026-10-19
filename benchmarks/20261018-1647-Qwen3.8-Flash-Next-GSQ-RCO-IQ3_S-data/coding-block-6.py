import tracemalloc

tracemalloc.start()
result = scan_large_files("/some/large/directory")
current, peak = tracemalloc.get_traced_memory()
print(f"current={current} peak={peak}")
tracemalloc.stop()
