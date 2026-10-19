import tracemalloc

tracemalloc.start(25)

result = scan_large_files(root)

snapshot = tracemalloc.take_snapshot()
for stat in snapshot.statistics("lineno")[:10]:
    print(stat)
